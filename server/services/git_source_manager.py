#!/usr/bin/env python3
"""
Git Source Manager - Service for cloning and managing git repositories
for Project Sources when mode=git.
"""
import os
import subprocess
import hashlib
import time
import threading
import fcntl
import ipaddress
import re as _re_url
import socket
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional, Tuple, Dict
import json
import logging

# logger git_source_manager
# root logger, backend.log
logger = logging.getLogger(__name__)
# , logger root logger
logger.setLevel(logging.INFO)
logger.propagate = True  # root logger backend.log
# handlers - root logger

# Try to import GlobalSecretsManager (may not be available)
try:
    from services.global_secrets_manager import GlobalSecretsManager, GlobalSecretError
    GLOBAL_SECRETS_AVAILABLE = True
except ImportError:
    GLOBAL_SECRETS_AVAILABLE = False
    GlobalSecretsManager = None
    GlobalSecretError = Exception


class GitSourceError(Exception):
    """Base exception for Git source operations"""
    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")


_ALLOWED_GIT_SCHEMES = {'https', 'http', 'ssh', 'git'}
_CREDS_IN_URL_RE = _re_url.compile(r'(https?://)([^/\s@]+)@', _re_url.IGNORECASE)


def _scrub_credentials(text: str) -> str:
    """Remove user:password@ segments from any URL in the given text."""
    if not text:
        return text
    return _CREDS_IN_URL_RE.sub(r'\1***@', text)


def validate_git_repo_url(url: str) -> None:
    """Reject schemes/hosts that could enable SSRF or local-file disclosure.

    Blocks ``file://``, loopback/private/link-local IPs, and the cloud metadata
    endpoint (169.254.169.254). Hostnames that resolve to private IPs are
    rejected too. Raises ``GitSourceError('GIT_INVALID_URL', ...)`` on failure.
    """
    if not url or not isinstance(url, str):
        raise GitSourceError('GIT_INVALID_URL', 'Repository URL is required')
    # Reject SCP-style git@host:path only if it tries scheme tricks.
    parsed = urlparse(url)
    scheme = (parsed.scheme or '').lower()
    if scheme and scheme not in _ALLOWED_GIT_SCHEMES:
        raise GitSourceError('GIT_INVALID_URL', f'Disallowed URL scheme: {scheme}')
    hostname = (parsed.hostname or '').strip()
    if not hostname and scheme:
        raise GitSourceError('GIT_INVALID_URL', 'Repository URL is missing a host')
    if not hostname:
        return  # SCP-style (git@host:path); host handled elsewhere
    # Block obvious metadata hostnames.
    if hostname.lower() in ('metadata', 'metadata.google.internal'):
        raise GitSourceError('GIT_INVALID_URL', f'Host not allowed: {hostname}')
    # Resolve and check IPs (covers DNS-rebinding-style hosts).
    candidates = []
    try:
        candidates.append(ipaddress.ip_address(hostname))
    except ValueError:
        try:
            for info in socket.getaddrinfo(hostname, None):
                try:
                    candidates.append(ipaddress.ip_address(info[4][0]))
                except ValueError:
                    continue
        except socket.gaierror:
            # Let git surface the DNS error instead of failing here.
            return
    for addr in candidates:
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_reserved or addr.is_unspecified):
            raise GitSourceError('GIT_INVALID_URL',
                                 f'Host resolves to a non-public address: {addr}')


class GitSourceManager:
    """
    Manages git repository cloning, fetching, and path resolution for Project Sources.
    
    Features:
    - Deterministic cache directory per project+repo
    - Ref (branch/tag/commit) checkout support
    - Subdir support within repo
    - Concurrency-safe with per-repo locks
    - Stale cache detection (60s default)
    - Path traversal prevention
    - Authentication via authSecretId
    """
    
    # Cache staleness threshold (seconds)
    CACHE_STALE_THRESHOLD = 60
    
    def __init__(self, base_cache_dir: Path, projects_dir: Path, base_dir: Optional[Path] = None):
        """
        Initialize GitSourceManager.
        
        Args:
            base_cache_dir: Base directory for git cache (e.g. .cache/git)
            projects_dir: Base directory for projects (for accessing secrets)
            base_dir: Base directory for global secrets (optional, defaults to projects_dir.parent)
        """
        self.base_cache_dir = Path(base_cache_dir)
        self.projects_dir = Path(projects_dir)
        self.base_cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize Global Secrets Manager if available
        self.global_secrets_manager = None
        if GLOBAL_SECRETS_AVAILABLE and GlobalSecretsManager:
            try:
                if base_dir is None:
                    base_dir = projects_dir.parent
                self.global_secrets_manager = GlobalSecretsManager(base_dir)
            except Exception as e:
                logger.warning(f"Failed to initialize Global Secrets Manager: {e}")
        
        # Per-repo locks for concurrency safety
        self._repo_locks: Dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
    
    def _get_repo_lock(self, repo_hash: str) -> threading.Lock:
        """Get or create a lock for a specific repository"""
        with self._locks_lock:
            if repo_hash not in self._repo_locks:
                self._repo_locks[repo_hash] = threading.Lock()
            return self._repo_locks[repo_hash]
    
    def _hash_repo_url(self, repo_url: str) -> str:
        """Generate deterministic hash for repo URL"""
        return hashlib.sha256(repo_url.encode('utf-8')).hexdigest()[:16]
    
    def _get_cache_dir(self, project_id: str, repo_url: str) -> Path:
        """Get cache directory for a project+repo combination"""
        repo_hash = self._hash_repo_url(repo_url)
        return self.base_cache_dir / project_id / repo_hash
    
    def _get_lock_file(self, cache_dir: Path) -> Path:
        """Get lock file path for a cache directory"""
        return cache_dir / '.git-source.lock'
    
    def _acquire_repo_lock(self, cache_dir: Path, timeout: int = 30) -> bool:
        """
        Acquire file-based lock for repository operations.
        Returns True if lock acquired, False if timeout.
        """
        lock_file = self._get_lock_file(cache_dir)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        
        start_time = time.time()
        lock_fd = None
        
        try:
            lock_file.touch(exist_ok=True)
            lock_fd = open(lock_file, 'w')
            
            while time.time() - start_time < timeout:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return True
                except IOError:
                    time.sleep(0.1)
            
            return False
        except Exception as e:
            logger.error(f"Error acquiring lock for {cache_dir}: {e}")
            return False
        finally:
            if lock_fd:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                except:
                    pass
                lock_fd.close()
    
    def _release_repo_lock(self, cache_dir: Path):
        """Release file-based lock"""
        lock_file = self._get_lock_file(cache_dir)
        if lock_file.exists():
            try:
                with open(lock_file, 'w') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except:
                pass
    
    def _is_cache_stale(self, cache_dir: Path) -> bool:
        """Check if cache is stale (older than threshold)"""
        if not cache_dir.exists():
            return True
        
        git_dir = cache_dir / '.git'
        if not git_dir.exists():
            return True
        
        # Check last fetch time from .git/FETCH_HEAD or .git/config
        fetch_head = git_dir / 'FETCH_HEAD'
        if fetch_head.exists():
            last_fetch = fetch_head.stat().st_mtime
            return (time.time() - last_fetch) > self.CACHE_STALE_THRESHOLD
        
        # If no FETCH_HEAD, check .git directory modification time
        git_mtime = git_dir.stat().st_mtime
        return (time.time() - git_mtime) > self.CACHE_STALE_THRESHOLD
    
    def _get_auth_env(self, project_id: str, auth_secret_id: Optional[str], repo_url: str = '') -> Dict[str, str]:
        """
        Get environment variables for git authentication.
        Returns dict with GIT_* env vars if authSecretId is set.
        
        Resolution order:
        1. Try Global Secrets Manager (if available)
        2. Fall back to project secrets (backward compatibility)
        
        If auth_secret_id is None or empty, returns empty env (no auth) - allows public repos.
        
        Raises:
            GitSourceError with error_code:
                - SECRET_NOT_FOUND: Secret not found in global or project storage
                - SECRET_TYPE_UNSUPPORTED: Secret type not supported for git auth
                - SECRET_DECRYPT_FAILED: Failed to decrypt secret material
                - GIT_AUTH_FAILED: General authentication failure
        """
        env = os.environ.copy()
        
        # If no auth_secret_id, return empty env (allows public repos)
        if not auth_secret_id or not auth_secret_id.strip():
            return env
        
        secret_data = None
        secret_type = None
        is_global_secret = False
        
        # Step 1: Try Global Secrets Manager first
        if self.global_secrets_manager:
            try:
                secret_data = self.global_secrets_manager.get_secret(
                    auth_secret_id, 
                    include_material=True  # Need decrypted material for git operations
                )
                if secret_data:
                    is_global_secret = True
                    secret_type = secret_data.get('type', '')
                    logger.info(f"Using global secret {auth_secret_id} (type: {secret_type}) for git auth")
                else:
                    # Secret not found (get_secret returns None if not found)
                    logger.debug(f"Global secret {auth_secret_id} not found, trying project secrets")
            except GlobalSecretError as e:
                # Secret not found or decryption failed
                error_msg = str(e)
                if 'not found' in error_msg.lower() or 'Failed to read' in error_msg:
                    logger.debug(f"Global secret {auth_secret_id} not found, trying project secrets")
                    # Don't fall through - raise error so we can try project secrets
                elif 'decrypt' in error_msg.lower() or 'Failed to decrypt' in error_msg:
                    logger.error(f"Failed to decrypt global secret {auth_secret_id}: {error_msg}")
                    raise GitSourceError('SECRET_DECRYPT_FAILED', f'Failed to decrypt secret: {error_msg}')
                else:
                    logger.warning(f"Error loading global secret {auth_secret_id}: {error_msg}")
                    # For other errors, try project secrets as fallback
            except Exception as e:
                logger.warning(f"Unexpected error loading global secret {auth_secret_id}: {e}")
                # For unexpected errors, try project secrets as fallback
        
        # Step 2: Fall back to project secrets (backward compatibility)
        if not secret_data:
            try:
                project_dir = self.projects_dir / project_id
                secrets_dir = project_dir / 'secrets'
                secret_file = None
                
                # : secrets/git_auth/<auth_id>.json
                # : secrets/git_auth/<auth_id>.json
                git_auth_dir = secrets_dir / 'git_auth'
                git_auth_file = git_auth_dir / f"{auth_secret_id}.json"
                if git_auth_file.exists():
                    secret_file = git_auth_file
                
                if not secret_file.exists():
                    raise GitSourceError('SECRET_NOT_FOUND', f'Secret {auth_secret_id} not found in global or project storage')
                
                with open(secret_file, 'r', encoding='utf-8') as f:
                    secret_data = json.load(f)
                
                secret_type = secret_data.get('type', '')
                logger.info(f"Using project secret {auth_secret_id} (type: {secret_type}) for git auth")
            
            except GitSourceError:
                raise
            except Exception as e:
                logger.error(f"Error loading project secret {auth_secret_id}: {e}")
                raise GitSourceError('GIT_AUTH_FAILED', f'Failed to load authentication secret: {str(e)}')
        
        # Validate secret type for git operations
        if not secret_type:
            raise GitSourceError('SECRET_TYPE_UNSUPPORTED', f'Secret {auth_secret_id} has no type specified')
        
        # Handle different secret types
        if secret_type == 'git_ssh_key' or (not is_global_secret and secret_type == 'ssh_key'):
            # SSH key authentication (global: git_ssh_key, project: ssh_key for backward compat)
            return self._setup_ssh_auth(secret_data, auth_secret_id)
        
        elif secret_type == 'git_token':
            # Token authentication (HTTPS)
            token = secret_data.get('token', '')
            if not token:
                raise GitSourceError('SECRET_TYPE_UNSUPPORTED', f'Secret {auth_secret_id} (type: {secret_type}) missing token')
            
            # For HTTPS with token, credentials will be embedded in URL during clone/fetch
            env['GIT_TERMINAL_PROMPT'] = '0'
            # Note: Token will be embedded in URL in _clone_or_fetch method
            return env
        
        elif not is_global_secret and secret_type == 'login_password':
            # HTTPS basic auth (project secrets only, for backward compatibility)
            username = secret_data.get('username', '')
            password = secret_data.get('password', '')
            if username and password:
                env['GIT_TERMINAL_PROMPT'] = '0'
                # Credentials will be embedded in URL during clone/fetch
                return env
        
        else:
            # Unsupported secret type
            raise GitSourceError(
                'SECRET_TYPE_UNSUPPORTED', 
                f'Secret type {secret_type} is not supported for git authentication. Supported: git_ssh_key, git_token'
            )
    
    def _setup_ssh_auth(self, secret_data: Dict, secret_id: str) -> Dict[str, str]:
        """
        Setup SSH key authentication environment.
        
        Args:
            secret_data: Secret data dictionary (with decrypted privateKey)
            secret_id: Secret ID (for logging only)
            
        Returns:
            Environment dictionary with GIT_SSH_COMMAND set
            
        Raises:
            GitSourceError if private key is missing or invalid
        """
        env = os.environ.copy()
        
        private_key = secret_data.get('privateKey', '')
        if not private_key:
            raise GitSourceError('SECRET_TYPE_UNSUPPORTED', f'Secret {secret_id} (type: git_ssh_key) missing privateKey')
        
        # Get username from metadata (for global secrets) or directly (for project secrets)
        metadata = secret_data.get('metadata', {})
        username = metadata.get('username') if metadata else secret_data.get('username', 'git')
        if not username:
            username = 'git'  # Default username for git
        
        passphrase = secret_data.get('passphrase', '')
        
        try:
            # Create temporary SSH key file
            import tempfile
            # Use .key extension instead of .pem for better compatibility
            temp_key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.key', encoding='utf-8')
            
            # Ensure key ends with newline (required by OpenSSH)
            key_content = private_key.strip()
            if not key_content.endswith('\n'):
                key_content += '\n'
            
            temp_key_file.write(key_content)
            temp_key_file.flush()  # Ensure data is written
            temp_key_file.close()
            
            # Set strict permissions (owner read/write only)
            os.chmod(temp_key_file.name, 0o600)
            
            # Build SSH command with username
            ssh_command = f'ssh -i {temp_key_file.name} -o StrictHostKeyChecking=accept-new'
            
            # Add username if specified and not default
            if username and username != 'git':
                ssh_command += f' -l {username}'
            
            if passphrase:
                # For passphrase-protected keys, we need to use ssh-agent
                # This is a simplified approach; in production, consider using ssh-agent
                ssh_command += ' -o BatchMode=yes'
                # Note: Passphrase handling requires ssh-agent or expect script
                # For now, we'll rely on ssh-agent if available
                logger.warning(f"Passphrase-protected key detected for secret {secret_id}. Ensure ssh-agent is configured.")
            
            env['GIT_SSH_COMMAND'] = ssh_command
            
            # Log that SSH auth is configured (but never log the key)
            logger.debug(f"SSH authentication configured for secret {secret_id} (key file: {temp_key_file.name}, username: {username})")
            
            return env
        
        except Exception as e:
            # Never log private key content
            logger.error(f"Failed to setup SSH authentication for secret {secret_id}: {str(e)}")
            raise GitSourceError('GIT_AUTH_FAILED', f'Failed to setup SSH authentication: {str(e)}')
    
    def _safe_subdir_path(self, base_path: Path, subdir: str) -> Path:
        """
        Safely resolve subdir path, preventing path traversal.
        Raises GitSourceError if path traversal detected.
        """
        if not subdir:
            return base_path
        
        # Normalize and resolve
        subdir_path = Path(subdir)
        
        # Prevent absolute paths
        if subdir_path.is_absolute():
            raise GitSourceError('INVALID_SUBDIR', 'Subdir cannot be an absolute path')
        
        # Prevent path traversal
        if '..' in subdir_path.parts:
            raise GitSourceError('INVALID_SUBDIR', 'Subdir contains path traversal (..)')
        
        # Resolve relative to base
        resolved = (base_path / subdir_path).resolve()
        
        # Ensure resolved path is within base_path
        try:
            resolved.relative_to(base_path.resolve())
        except ValueError:
            raise GitSourceError('INVALID_SUBDIR', 'Subdir resolves outside repository')
        
        return resolved
    
    def _clone_or_fetch(self, project_id: str, repo_url: str, ref: str, 
                       auth_secret_id: Optional[str], force_refresh: bool = False) -> Path:
        """
        Clone or fetch repository to cache.
        Returns path to cached repository.
        """
        # SSRF / local-file disclosure guard.
        validate_git_repo_url(repo_url)
        cache_dir = self._get_cache_dir(project_id, repo_url)
        repo_hash = self._hash_repo_url(repo_url)
        
        # Use threading lock for this repo
        lock = self._get_repo_lock(repo_hash)
        
        with lock:
            # Check if we need to clone/fetch
            needs_clone = not cache_dir.exists() or not (cache_dir / '.git').exists()
            needs_fetch = not needs_clone and (force_refresh or self._is_cache_stale(cache_dir))
            
            if needs_clone:
                # Clone repository
                logger.info(f"Cloning repository {repo_url} to {cache_dir}")
                cache_dir.parent.mkdir(parents=True, exist_ok=True)
                
                    # Get auth environment
                env = self._get_auth_env(project_id, auth_secret_id, repo_url)
                
                # Prepare repo URL with credentials if needed (for HTTPS with token/password)
                clone_url = repo_url
                if auth_secret_id and 'https://' in repo_url:
                    try:
                        # Try to get secret to embed credentials in URL
                        secret_data = None
                        
                        # Try global secret first
                        if self.global_secrets_manager:
                            try:
                                secret_data = self.global_secrets_manager.get_secret(
                                    auth_secret_id, 
                                    include_material=True
                                )
                            except Exception:
                                pass
                        
                        # Fall back to project secret
                        if not secret_data:
                            secrets_dir = self.projects_dir / project_id / 'secrets-storage'
                            secret_file = secrets_dir / f"{auth_secret_id}.json"
                            if secret_file.exists():
                                with open(secret_file, 'r', encoding='utf-8') as f:
                                    secret_data = json.load(f)
                        
                        if secret_data:
                            secret_type = secret_data.get('type', '')
                            
                            # Handle git_token (global) or login_password (project)
                            if secret_type == 'git_token':
                                token = secret_data.get('token', '')
                                username = secret_data.get('metadata', {}).get('username', '') or secret_data.get('username', '')
                                if token:
                                    # Embed token in URL
                                    from urllib.parse import urlparse, urlunparse
                                    parsed = urlparse(repo_url)
                                    if username:
                                        netloc = f"{username}:{token}@{parsed.netloc}"
                                    else:
                                        netloc = f"{token}@{parsed.netloc}"
                                    clone_url = urlunparse(parsed._replace(netloc=netloc))
                            
                            elif secret_type == 'login_password':
                                username = secret_data.get('username', '')
                                password = secret_data.get('password', '')
                                if username and password:
                                    # Embed credentials in URL
                                    from urllib.parse import urlparse, urlunparse
                                    parsed = urlparse(repo_url)
                                    netloc = f"{username}:{password}@{parsed.netloc}"
                                    clone_url = urlunparse(parsed._replace(netloc=netloc))
                    except Exception as e:
                        logger.warning(f"Failed to embed credentials in URL: {e}")
                
                try:
                    result = subprocess.run(
                        ['git', 'clone', '--depth', '1', '--no-single-branch', clone_url, str(cache_dir)],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        check=False
                    )
                    
                    if result.returncode != 0:
                        error_msg = _scrub_credentials(result.stderr or result.stdout or 'Unknown error')
                        if 'Authentication failed' in error_msg or 'Permission denied' in error_msg:
                            raise GitSourceError('GIT_AUTH_FAILED', f'Authentication failed: {error_msg}')
                        raise GitSourceError('GIT_CLONE_FAILED', f'Clone failed: {error_msg}')
                except subprocess.TimeoutExpired:
                    raise GitSourceError('GIT_CLONE_FAILED', 'Clone operation timed out')
                except GitSourceError:
                    raise
                except Exception as e:
                    raise GitSourceError('GIT_CLONE_FAILED', f'Clone failed: {_scrub_credentials(str(e))}')
            
            # Fetch updates if needed
            if needs_fetch:
                logger.info(f"[PULL] Fetching updates for {repo_url}")
                env = self._get_auth_env(project_id, auth_secret_id, repo_url)
                
                try:
                    # Fetch all branches and tags
                    logger.info(f"[PULL] Running: git fetch --all --prune")
                    result = subprocess.run(
                        ['git', 'fetch', '--all', '--prune'],
                        cwd=cache_dir,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=False
                    )
                    
                    if result.returncode != 0:
                        error_msg = result.stderr or result.stdout or 'Unknown error'
                        logger.error(f"[PULL] git fetch failed: {error_msg}")
                        if 'Authentication failed' in error_msg or 'Permission denied' in error_msg:
                            raise GitSourceError('GIT_AUTH_FAILED', f'Authentication failed: {error_msg}')
                        logger.warning(f"[PULL] Fetch failed (non-fatal): {error_msg}")
                    else:
                        logger.info(f"[PULL] Fetch completed successfully for {repo_url}")
                        if result.stdout:
                            logger.debug(f"[PULL] Fetch output: {result.stdout}")
                except subprocess.TimeoutExpired:
                    logger.warning("Fetch operation timed out (non-fatal)")
                except GitSourceError:
                    raise
            
            # Checkout ref (always checkout after fetch to ensure we have latest changes)
            if ref:
                logger.info(f"[PULL] Checking out ref {ref} in {cache_dir}")
                try:
                    # If we just fetched, reset hard to ensure we have latest changes
                    if needs_fetch:
                        # Try to reset to remote branch to get latest changes
                        reset_result = subprocess.run(
                            ['git', 'reset', '--hard', f'origin/{ref}'],
                            cwd=cache_dir,
                            capture_output=True,
                            text=True,
                            timeout=30,
                            check=False
                        )
                        if reset_result.returncode == 0:
                            logger.info(f"[PULL] Reset to origin/{ref} after fetch")
                        else:
                            # If reset failed (e.g., ref is a tag or commit), try regular checkout
                            logger.info(f"[PULL] Reset failed, trying checkout {ref}")
                            result = subprocess.run(
                                ['git', 'checkout', ref],
                                cwd=cache_dir,
                                capture_output=True,
                                text=True,
                                timeout=30,
                                check=False
                            )
                            if result.returncode != 0:
                                error_msg = result.stderr or result.stdout or 'Unknown error'
                                logger.error(f"[PULL] git checkout failed: {error_msg}")
                                raise GitSourceError('GIT_REF_NOT_FOUND', f'Ref {ref} not found: {error_msg}')
                            logger.info(f"[PULL] Checkout {ref} successful")
                    else:
                        # Regular checkout if no fetch was needed
                        result = subprocess.run(
                            ['git', 'checkout', ref],
                            cwd=cache_dir,
                            capture_output=True,
                            text=True,
                            timeout=30,
                            check=False
                        )
                        
                        if result.returncode != 0:
                            error_msg = result.stderr or result.stdout or 'Unknown error'
                            logger.warning(f"[PULL] Initial checkout failed: {error_msg}, trying to fetch ref first")
                            # Try to fetch the ref first
                            logger.info(f"[PULL] Running: git fetch origin {ref}")
                            fetch_result = subprocess.run(
                                ['git', 'fetch', 'origin', ref],
                                cwd=cache_dir,
                                env=self._get_auth_env(project_id, auth_secret_id, repo_url),
                                capture_output=True,
                                text=True,
                                timeout=60,
                                check=False
                            )
                            
                            if fetch_result.returncode == 0:
                                logger.info(f"[PULL] Fetch of ref {ref} successful, retrying checkout")
                                # Retry checkout
                                result = subprocess.run(
                                    ['git', 'checkout', ref],
                                    cwd=cache_dir,
                                    capture_output=True,
                                    text=True,
                                    timeout=30,
                                    check=False
                                )
                            else:
                                fetch_error = fetch_result.stderr or fetch_result.stdout or 'Unknown error'
                                logger.error(f"[PULL] Fetch of ref {ref} failed: {fetch_error}")
                        
                        if result.returncode != 0:
                            error_msg = result.stderr or result.stdout or 'Unknown error'
                            logger.error(f"[PULL] git checkout failed: {error_msg}")
                            raise GitSourceError('GIT_REF_NOT_FOUND', f'Ref {ref} not found: {error_msg}')
                        logger.info(f"[PULL] Checkout {ref} successful")
                except subprocess.TimeoutExpired:
                    raise GitSourceError('GIT_REF_NOT_FOUND', f'Checkout of ref {ref} timed out')
                except GitSourceError:
                    raise
                except Exception as e:
                    raise GitSourceError('GIT_REF_NOT_FOUND', f'Failed to checkout ref {ref}: {str(e)}')
            
            return cache_dir
    
    def resolve_path(self, project_id: str, source_key: str, repo_url: str, ref: str = 'main',
                    subdir: str = '', auth_secret_id: Optional[str] = None,
                    force_refresh: bool = False) -> Path:
        """
        Resolve absolute path on disk for a git source.
        
        Args:
            project_id: Project ID
            source_key: Source key (e.g. 'roles_playbooks', 'inventory')
            repo_url: Git repository URL
            ref: Git ref (branch/tag/commit), default 'main'
            subdir: Subdirectory within repo, default ''
            auth_secret_id: Optional secret ID for authentication
            force_refresh: Force refresh even if cache is fresh
        
        Returns:
            Absolute Path to the resolved directory/file
        
        Raises:
            GitSourceError with error_code:
                - GIT_AUTH_FAILED: Authentication failed
                - GIT_REF_NOT_FOUND: Ref not found
                - GIT_CLONE_FAILED: Clone operation failed
                - SUBDIR_NOT_FOUND: Subdir not found
                - INVALID_SUBDIR: Invalid subdir (path traversal)
        """
        # Clone or fetch repository
        cache_dir = self._clone_or_fetch(project_id, repo_url, ref, auth_secret_id, force_refresh)
        
        # Resolve subdir if specified
        if subdir:
            try:
                resolved_path = self._safe_subdir_path(cache_dir, subdir)
                
                # Check if subdir exists
                if not resolved_path.exists():
                    raise GitSourceError('SUBDIR_NOT_FOUND', f'Subdir {subdir} not found in repository')
                
                return resolved_path
            except GitSourceError:
                raise
            except Exception as e:
                raise GitSourceError('INVALID_SUBDIR', f'Invalid subdir: {str(e)}')
        
        return cache_dir

