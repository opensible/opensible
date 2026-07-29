#!/usr/bin/env python3
"""Documentation."""
from typing import List, Optional, Set
from pathlib import Path
import logging

try:
    from services.user_service import UserService
    from services.role_service import RoleService, PermissionService as PermissionServiceClass
except ImportError:
    from services.user_service import UserService
    from services.role_service import RoleService, PermissionService as PermissionServiceClass

logger = logging.getLogger(__name__)


class AccessControlService:
    """Documentation."""
    
    def __init__(self, data_dir: Path):
        """Documentation."""
        self.data_dir = data_dir
        self.user_service = UserService(data_dir)
        self.role_service = RoleService(data_dir)
        self.permission_service = PermissionServiceClass(data_dir)
    
    def get_user_permissions(self, user_id: str) -> Set[str]:
        """Documentation."""
        user = self.user_service.get_user_by_id(user_id)
        if not user or not user.is_active:
            return set()
        
        permissions = set()
        
        # Comment removed.
        for role_id in user.roles:
            role = self.role_service.get_role_by_id(role_id)
            if role:
                # Comment removed.
                for perm_id in role.permissions:
                    perm = self.permission_service.get_permission_by_id(perm_id)
                    if perm:
                        permissions.add(perm.name)
        
        return permissions
    
    def has_permission(self, user_id: str, permission_name: str) -> bool:
        """Documentation."""
        user_permissions = self.get_user_permissions(user_id)
        return permission_name in user_permissions
    
    def has_any_permission(self, user_id: str, permission_names: List[str]) -> bool:
        """Documentation."""
        user_permissions = self.get_user_permissions(user_id)
        return bool(user_permissions & set(permission_names))
    
    def has_all_permissions(self, user_id: str, permission_names: List[str]) -> bool:
        """Documentation."""
        user_permissions = self.get_user_permissions(user_id)
        return set(permission_names).issubset(user_permissions)
    
    def get_user_roles(self, user_id: str) -> List[str]:
        """Documentation."""
        user = self.user_service.get_user_by_id(user_id)
        if not user or not user.is_active:
            return []
        
        role_names = []
        for role_id in user.roles:
            role = self.role_service.get_role_by_id(role_id)
            if role:
                role_names.append(role.name)
        
        return role_names
    
    def has_role(self, user_id: str, role_name: str) -> bool:
        """Documentation."""
        user_roles = self.get_user_roles(user_id)
        return role_name in user_roles
    
    def has_any_role(self, user_id: str, role_names: List[str]) -> bool:
        """Documentation."""
        user_roles = self.get_user_roles(user_id)
        return bool(set(user_roles) & set(role_names))
    
    def check_resource_access(self, user_id: str, resource: str, action: str) -> bool:
        """Documentation."""
        permission_name = f"{resource}.{action}"
        return self.has_permission(user_id, permission_name)
