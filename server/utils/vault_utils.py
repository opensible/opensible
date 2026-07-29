"""
Utilities for ansible-vault encryption/decryption and vault key management.
"""
import os
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, Tuple


def is_ansible_vault_encrypted(content: str) -> bool:
    """Check if content is ansible-vault encrypted."""
    if not content or not isinstance(content, str):
        return False
    return content.strip().startswith('$ANSIBLE_VAULT')


def parse_vault_id_from_header(content: str) -> Optional[str]:
    """
    Extract vault ID label from ansible-vault encrypted content header.
    
    Format: $ANSIBLE_VAULT;1.1;AES256;777  (vault_id is 4th element after ';')
    Or:     $ANSIBLE_VAULT;1.2;AES256;prod
    Or:     $ANSIBLE_VAULT;1.1;AES256 (no vault_id, returns None)
    
    Returns:
        vault_id label if present (e.g. "777", "prod"), None otherwise
    """
    if not content or not isinstance(content, str):
        return None
    line = content.strip().split('\n')[0]
    if not line.startswith('$ANSIBLE_VAULT'):
        return None
    parts = line.split(';')
    if len(parts) >= 4:
        return parts[3].strip() or None
    return None


def ansible_vault_decrypt(encrypted_content: str, password: str, vault_id: Optional[str] = None) -> Tuple[bool, str]:
    """
    Decrypt ansible-vault encrypted content using ansible-vault.
    
    Args:
        encrypted_content: The encrypted string (from file)
        password: Vault password
        vault_id: Optional vault ID label (for multi-vault, format: --vault-id label@file)
        
    Returns:
        (success, decrypted_content or error_message)
    """
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as enc_f:
            enc_f.write(encrypted_content)
            enc_path = enc_f.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pass', delete=False) as pass_f:
            pass_f.write(password)
            if not password.endswith('\n'):
                pass_f.write('\n')
            pass_path = pass_f.name
        try:
            os.chmod(pass_path, 0o600)
            cmd = ['ansible-vault', 'decrypt', enc_path, '--output', '-']
            if vault_id:
                cmd.extend(['--vault-id', f'{vault_id}@{pass_path}'])
            else:
                cmd.extend(['--vault-password-file', pass_path])
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                return False, result.stderr or result.stdout or 'Decryption failed'
            return True, result.stdout
        finally:
            try:
                os.unlink(enc_path)
            except OSError:
                pass
            try:
                os.unlink(pass_path)
            except OSError:
                pass
    except subprocess.TimeoutExpired:
        return False, 'Decryption timeout'
    except FileNotFoundError:
        return False, 'ansible-vault command not found. Install ansible.'
    except Exception as e:
        return False, str(e)


def ansible_vault_encrypt(plain_content: str, password: str, vault_id: Optional[str] = None) -> Tuple[bool, str]:
    """
    Encrypt content with ansible-vault.
    
    Args:
        plain_content: Plain text to encrypt
        password: Vault password
        vault_id: Optional vault ID (for multi-vault scenarios)
        
    Returns:
        (success, encrypted_content or error_message)
    """
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as plain_f:
            plain_f.write(plain_content)
            plain_path = plain_f.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pass', delete=False) as pass_f:
            pass_f.write(password)
            if not password.endswith('\n'):
                pass_f.write('\n')
            pass_path = pass_f.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as out_f:
            out_path = out_f.name
        try:
            os.chmod(pass_path, 0o600)
            if vault_id:
                cmd = ['ansible-vault', 'encrypt', plain_path, '--output', out_path,
                       '--vault-id', f'{vault_id}@{pass_path}', '--encrypt-vault-id', vault_id]
            else:
                cmd = ['ansible-vault', 'encrypt', plain_path, '--output', out_path,
                       '--vault-password-file', pass_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return False, result.stderr or result.stdout or 'Encryption failed'
            with open(out_path, 'r', encoding='utf-8') as f:
                encrypted = f.read()
            return True, encrypted
        finally:
            for p in (plain_path, pass_path, out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    except subprocess.TimeoutExpired:
        return False, 'Encryption timeout'
    except FileNotFoundError:
        return False, 'ansible-vault command not found. Install ansible.'
    except Exception as e:
        return False, str(e)
