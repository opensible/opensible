"""
Global Secrets Manager - Backend storage and CRUD operations.

Manages organization-wide secrets for platform integrations (Git, registries, vault).
Secrets are stored encrypted at rest in a dedicated directory outside project storage.
"""
import os
import json
import uuid
import time
import re
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime

from utils.secret_encryption import get_encryption


# Secret types supported
SECRET_TYPES = {
    'git_ssh_key': {
        'required_fields': ['privateKey'],
        'optional_fields': ['passphrase', 'username', 'publicKey', 'fingerprint'],
        'metadata_fields': ['username', 'keyType', 'keySize', 'comment', 'fingerprint']
    },
    'git_token': {
        'required_fields': ['token'],
        'optional_fields': ['username'],
        'metadata_fields': ['provider', 'username', 'scope', 'expiresAt', 'tokenPrefix']
    },
    'basic_auth': {
        'required_fields': ['username', 'password'],
        'optional_fields': [],
        'metadata_fields': ['username', 'registry']
    },
    'registry_token': {
        'required_fields': ['token'],
        'optional_fields': [],
        'metadata_fields': ['registry', 'tokenType', 'expiresAt', 'tokenPrefix']
    },
    'vault_token': {
        'required_fields': ['token'],
        'optional_fields': [],
        'metadata_fields': ['vaultUrl', 'policy', 'expiresAt', 'tokenPrefix']
    }
}

# Fields that contain secret material (must be encrypted)
SECRET_MATERIAL_FIELDS = ['privateKey', 'passphrase', 'token', 'password']


class GlobalSecretError(Exception):
    """Base exception for Global Secrets Manager errors"""
    pass


class GlobalSecretsManager:
    """
    Manages global secrets storage and retrieval.
    
    Secrets are stored in {DATA_ROOT}/global/secrets/ directory.
    Each secret is stored as a JSON file with encrypted secret material.
    """
    
    def __init__(self, data_dir: Path):
        """
        Initialize Global Secrets Manager.
        
        Args:
            data_dir: Data directory (typically DATA_DIR from app.py, which is BASE_DIR / 'data')
        """
        self.data_dir = Path(data_dir)
        self.secrets_dir = self.data_dir / 'global' / 'secrets'
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        
        # Set directory permissions (owner only)
        os.chmod(self.secrets_dir, 0o700)
        
        # Initialize encryption with data_dir for key file management
        self.encryption = get_encryption(self.data_dir)
    
    def _get_secret_file_path(self, secret_id: str) -> Path:
        """
        Get file path for a secret.
        
        Args:
            secret_id: Secret UUID
            
        Returns:
            Path to secret file
            
        Raises:
            GlobalSecretError: If secret_id contains path traversal
        """
        # Prevent path traversal
        if '..' in secret_id or '/' in secret_id or '\\' in secret_id:
            raise GlobalSecretError(f"Invalid secret ID: {secret_id}")
        
        return self.secrets_dir / f"{secret_id}.json"
    
    def _validate_secret_name(self, name: str) -> Tuple[bool, Optional[str]]:
        """
        Validate secret name.
        
        Args:
            name: Secret name to validate
            
        Returns:
            (is_valid, error_message)
        """
        if not name or not isinstance(name, str):
            return False, "Secret name is required"
        
        name = name.strip()
        
        if len(name) < 3:
            return False, "Secret name must be at least 3 characters"
        
        if len(name) > 64:
            return False, "Secret name must be at most 64 characters"
        
        # Allow alphanumeric, hyphens, underscores
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return False, "Secret name can only contain letters, numbers, hyphens, and underscores"
        
        return True, None
    
    def _validate_secret_type(self, secret_type: str) -> Tuple[bool, Optional[str]]:
        """
        Validate secret type.
        
        Args:
            secret_type: Secret type to validate
            
        Returns:
            (is_valid, error_message)
        """
        if secret_type not in SECRET_TYPES:
            valid_types = ', '.join(SECRET_TYPES.keys())
            return False, f"Invalid secret type. Must be one of: {valid_types}"
        
        return True, None
    
    def _validate_secret_data(self, secret_type: str, data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Validate secret data according to type requirements.
        
        Args:
            secret_type: Type of secret
            data: Secret data dictionary
            
        Returns:
            (is_valid, error_message)
        """
        type_config = SECRET_TYPES.get(secret_type)
        if not type_config:
            return False, f"Unknown secret type: {secret_type}"
        
        # Check required fields
        for field in type_config['required_fields']:
            if field not in data or not data[field] or not str(data[field]).strip():
                return False, f"Required field '{field}' is missing or empty"
        
        # Type-specific validation
        if secret_type == 'git_ssh_key':
            private_key = data.get('privateKey', '').strip()
            if private_key:
                # Import validation function from app.py
                try:
                    from .app import validate_ssh_private_key
                    is_valid, error_msg = validate_ssh_private_key(private_key)
                    if not is_valid:
                        return False, f"Invalid SSH private key format: {error_msg}"
                except ImportError:
                    # Fallback validation if app.py not available
                    if not private_key.startswith('-----BEGIN') or not private_key.endswith('-----'):
                        return False, "Invalid SSH private key format"
        
        return True, None
    
    def _encrypt_secret_material(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Encrypt secret material fields in data dictionary.
        
        Args:
            data: Secret data dictionary
            
        Returns:
            Dictionary with encrypted secret material
        """
        encrypted_data = data.copy()
        
        for field in SECRET_MATERIAL_FIELDS:
            if field in encrypted_data and encrypted_data[field]:
                try:
                    plaintext = str(encrypted_data[field])
                    if plaintext:  # Only encrypt non-empty strings
                        encrypted_data[field] = self.encryption.encrypt(plaintext)
                except Exception as e:
                    raise GlobalSecretError(f"Failed to encrypt {field}: {str(e)}")
        
        return encrypted_data
    
    def _decrypt_secret_material(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decrypt secret material fields in data dictionary.
        
        Args:
            data: Secret data dictionary with encrypted fields
            
        Returns:
            Dictionary with decrypted secret material
            
        Raises:
            GlobalSecretError: If decryption fails
        """
        decrypted_data = data.copy()
        
        for field in SECRET_MATERIAL_FIELDS:
            if field in decrypted_data and decrypted_data[field]:
                try:
                    encrypted = str(decrypted_data[field])
                    decrypted_data[field] = self.encryption.decrypt(encrypted)
                except ValueError as e:
                    # ValueError from decrypt() contains detailed message about encryption key
                    raise GlobalSecretError(f"Failed to decrypt {field}: {str(e)}")
                except Exception as e:
                    # Other decryption errors
                    raise GlobalSecretError(f"Failed to decrypt {field}: {str(e)}")
        
        return decrypted_data
    
    def _sanitize_secret_for_api(self, secret_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove secret material from secret data for API responses.
        
        Args:
            secret_data: Full secret data dictionary
            
        Returns:
            Dictionary with only metadata (no secret material)
        """
        sanitized = secret_data.copy()
        
        # Remove secret material fields
        for field in SECRET_MATERIAL_FIELDS:
            sanitized.pop(field, None)
        
        # Add masked values for display
        if 'token' in secret_data:
            token = secret_data.get('token', '')
            if token:
                # Show first 4-8 characters + ***
                prefix = token[:min(8, len(token))]
                sanitized['metadata'] = sanitized.get('metadata', {})
                sanitized['metadata']['tokenPrefix'] = f"{prefix}***"
        
        return sanitized
    
    def _generate_secret_id(self) -> str:
        """Generate a unique secret ID (UUID)"""
        return str(uuid.uuid4())
    
    def _get_current_user(self) -> Optional[str]:
        """
        Get current user identifier (for createdBy field).
        
        Returns:
            Username or user identifier, or None if not available
        """
        # Try to get from environment or request context
        # This is a placeholder - implement based on your auth system
        return os.environ.get('USER') or os.environ.get('USERNAME') or None
    
    def list_secrets(self, secret_type: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all global secrets (metadata only, no secret material).
        
        Args:
            secret_type: Optional filter by type
            search: Optional search term (searches in name and description)
            
        Returns:
            List of secret metadata dictionaries
        """
        secrets = []
        
        # Iterate through all secret files
        for secret_file in self.secrets_dir.glob('*.json'):
            try:
                with open(secret_file, 'r', encoding='utf-8') as f:
                    secret_data = json.load(f)
                
                # Filter by type if specified
                if secret_type and secret_data.get('type') != secret_type:
                    continue
                
                # Filter by search term if specified
                if search:
                    search_lower = search.lower()
                    name = secret_data.get('name', '').lower()
                    description = secret_data.get('description', '').lower()
                    if search_lower not in name and search_lower not in description:
                        continue
                
                # Sanitize (remove secret material)
                sanitized = self._sanitize_secret_for_api(secret_data)
                secrets.append(sanitized)
            
            except Exception as e:
                # Skip corrupted files, log error
                import logging
                logging.warning(f"Error reading secret file {secret_file}: {e}")
                continue
        
        # Sort by name
        secrets.sort(key=lambda x: x.get('name', '').lower())
        
        return secrets
    
    def get_secret(self, secret_id: str, include_material: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get secret by ID.
        
        Args:
            secret_id: Secret UUID
            include_material: If True, include decrypted secret material (backend only)
            
        Returns:
            Secret data dictionary, or None if not found
        """
        secret_file = self._get_secret_file_path(secret_id)
        
        if not secret_file.exists():
            return None
        
        try:
            with open(secret_file, 'r', encoding='utf-8') as f:
                secret_data = json.load(f)
            
            # Decrypt secret material if requested
            if include_material:
                try:
                    secret_data = self._decrypt_secret_material(secret_data)
                except Exception as decrypt_error:
                    # If decryption fails, provide more specific error
                    raise GlobalSecretError(f"Failed to decrypt secret {secret_id}: {str(decrypt_error)}")
            else:
                # Sanitize (remove secret material)
                secret_data = self._sanitize_secret_for_api(secret_data)
            
            return secret_data
        
        except GlobalSecretError:
            # Re-raise GlobalSecretError as-is
            raise
        except Exception as e:
            raise GlobalSecretError(f"Failed to read secret {secret_id}: {str(e)}")
    
    def create_secret(self, name: str, secret_type: str, data: Dict[str, Any], 
                     description: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a new global secret.
        
        Args:
            name: Secret name (must be unique)
            secret_type: Type of secret (git_ssh_key, git_token, etc.)
            data: Secret data dictionary (includes secret material)
            description: Optional description
            
        Returns:
            Created secret metadata (no secret material)
            
        Raises:
            GlobalSecretError: If validation fails or name is not unique
        """
        # Validate name
        is_valid, error_msg = self._validate_secret_name(name)
        if not is_valid:
            raise GlobalSecretError(error_msg)
        
        # Validate type
        is_valid, error_msg = self._validate_secret_type(secret_type)
        if not is_valid:
            raise GlobalSecretError(error_msg)
        
        # Validate data
        is_valid, error_msg = self._validate_secret_data(secret_type, data)
        if not is_valid:
            raise GlobalSecretError(error_msg)
        
        # Check name uniqueness
        existing_secrets = self.list_secrets()
        for existing in existing_secrets:
            if existing.get('name', '').lower() == name.lower():
                raise GlobalSecretError(f"Secret with name '{name}' already exists")
        
        # Generate ID
        secret_id = self._generate_secret_id()
        
        # Prepare secret data
        now = datetime.utcnow().isoformat() + 'Z'
        secret_data = {
            'id': secret_id,
            'name': name.strip(),
            'type': secret_type,
            'description': (description.strip() if description else '') or '',
            'createdAt': now,
            'updatedAt': now,
            'createdBy': self._get_current_user() or '',
            'version': 1,
            'scope': 'global',
            'metadata': {}
        }
        
        # Add type-specific metadata
        type_config = SECRET_TYPES.get(secret_type, {})
        metadata_fields = type_config.get('metadata_fields', [])
        for field in metadata_fields:
            if field in data:
                secret_data['metadata'][field] = data[field]
        
        # Add secret material (will be encrypted)
        for field in type_config.get('required_fields', []) + type_config.get('optional_fields', []):
            if field in data and data[field]:
                secret_data[field] = data[field]
        
        # Encrypt secret material
        secret_data = self._encrypt_secret_material(secret_data)
        
        # Save to file
        secret_file = self._get_secret_file_path(secret_id)
        try:
            with open(secret_file, 'w', encoding='utf-8') as f:
                json.dump(secret_data, f, indent=2, ensure_ascii=False)
            
            # Set file permissions (owner read/write only)
            os.chmod(secret_file, 0o600)
        
        except Exception as e:
            raise GlobalSecretError(f"Failed to save secret: {str(e)}")
        
        # Return sanitized version (no secret material)
        return self._sanitize_secret_for_api(secret_data)
    
    def update_secret(self, secret_id: str, name: Optional[str] = None,
                     description: Optional[str] = None,
                     metadata: Optional[Dict[str, Any]] = None,
                     secret_material: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Update an existing global secret.
        
        Args:
            secret_id: Secret UUID
            name: Optional new name
            description: Optional new description
            metadata: Optional metadata updates
            secret_material: Optional secret material updates (e.g., new token, new private key)
            
        Returns:
            Updated secret metadata (no secret material)
            
        Raises:
            GlobalSecretError: If secret not found or validation fails
        """
        # Load existing secret
        secret_file = self._get_secret_file_path(secret_id)
        if not secret_file.exists():
            raise GlobalSecretError(f"Secret {secret_id} not found")
        
        with open(secret_file, 'r', encoding='utf-8') as f:
            secret_data = json.load(f)
        
        # Decrypt for updates
        secret_data = self._decrypt_secret_material(secret_data)
        
        secret_type = secret_data.get('type')
        
        # Update name if provided
        if name is not None:
            is_valid, error_msg = self._validate_secret_name(name)
            if not is_valid:
                raise GlobalSecretError(error_msg)
            
            # Check name uniqueness (excluding current secret)
            existing_secrets = self.list_secrets()
            for existing in existing_secrets:
                if existing.get('id') != secret_id and existing.get('name', '').lower() == name.lower():
                    raise GlobalSecretError(f"Secret with name '{name}' already exists")
            
            secret_data['name'] = name.strip()
        
        # Update description if provided
        if description is not None:
            secret_data['description'] = description.strip()
        
        # Update metadata if provided
        if metadata is not None:
            if 'metadata' not in secret_data:
                secret_data['metadata'] = {}
            secret_data['metadata'].update(metadata)
        
        # Update secret material if provided
        if secret_material is not None:
            # Validate new secret material
            combined_data = secret_data.copy()
            combined_data.update(secret_material)
            is_valid, error_msg = self._validate_secret_data(secret_type, combined_data)
            if not is_valid:
                raise GlobalSecretError(error_msg)
            
            # Update secret material fields
            for field, value in secret_material.items():
                if field in SECRET_MATERIAL_FIELDS or field in SECRET_TYPES.get(secret_type, {}).get('optional_fields', []):
                    secret_data[field] = value
        
        # Update timestamp
        secret_data['updatedAt'] = datetime.utcnow().isoformat() + 'Z'
        
        # Encrypt secret material
        secret_data = self._encrypt_secret_material(secret_data)
        
        # Save to file
        try:
            with open(secret_file, 'w', encoding='utf-8') as f:
                json.dump(secret_data, f, indent=2, ensure_ascii=False)
            
            # Set file permissions
            os.chmod(secret_file, 0o600)
        
        except Exception as e:
            raise GlobalSecretError(f"Failed to update secret: {str(e)}")
        
        # Return sanitized version
        return self._sanitize_secret_for_api(secret_data)
    
    def delete_secret(self, secret_id: str) -> bool:
        """
        Delete a global secret.
        
        Args:
            secret_id: Secret UUID
            
        Returns:
            True if deleted, False if not found
            
        Raises:
            GlobalSecretError: If deletion fails
        """
        secret_file = self._get_secret_file_path(secret_id)
        
        if not secret_file.exists():
            return False
        
        try:
            secret_file.unlink()
            return True
        except Exception as e:
            raise GlobalSecretError(f"Failed to delete secret {secret_id}: {str(e)}")
    
    def get_secret_options(self, purpose: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get minimal secret list for dropdowns (id, name, type, short meta).
        
        Args:
            purpose: Optional filter by purpose (e.g., 'git' for git_ssh_key and git_token)
            
        Returns:
            List of minimal secret dictionaries
        """
        secrets = self.list_secrets()
        
        # Filter by purpose if specified
        if purpose == 'git':
            filtered_secrets = [s for s in secrets if s.get('type') in ['git_ssh_key', 'git_token']]
            # Log for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"get_secret_options: purpose=git, total secrets: {len(secrets)}, filtered: {len(filtered_secrets)}")
            # Log all secret types for debugging
            if len(filtered_secrets) == 0 and len(secrets) > 0:
                all_types = [s.get('type') for s in secrets]
                logger.warning(f"get_secret_options: No git secrets found. Available types: {all_types}")
                # Fallback: if no git secrets found but we have secrets, check if any have 'ssh' in type
                # This handles legacy secrets that might have been created with different type names
                ssh_secrets = [s for s in secrets if 'ssh' in s.get('type', '').lower() or 'key' in s.get('type', '').lower()]
                if ssh_secrets:
                    logger.info(f"get_secret_options: Found {len(ssh_secrets)} SSH-related secrets, including them as fallback")
                    filtered_secrets = ssh_secrets
            secrets = filtered_secrets
        
        # Return minimal fields
        options = []
        for secret in secrets:
            option = {
                'id': secret.get('id'),
                'name': secret.get('name'),
                'type': secret.get('type'),
                'meta': secret.get('metadata', {})
            }
            options.append(option)
        
        return options

