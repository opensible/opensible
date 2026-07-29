#!/usr/bin/env python3
"""
 RBAC
"""
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
from datetime import datetime
import json


@dataclass
class Permission:
    """ """
    id: str
    name: str
    description: str
    resource: str  # , (, 'users', 'roles', 'projects')
    action: str    # (, 'create', 'read', 'update', 'delete')
    created_at: Optional[str] = None
    
    def to_dict(self) -> dict:
        """ """
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Permission':
        """ """
        return cls(**data)


@dataclass
class Role:
    """ """
    id: str
    name: str
    description: str
    permissions: List[str]  # ID 
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    
    def to_dict(self) -> dict:
        """ """
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Role':
        """ """
        return cls(**data)
    
    def has_permission(self, permission_id: str) -> bool:
        """, """
        return permission_id in self.permissions


@dataclass
class User:
    """ """
    id: str
    username: str
    email: Optional[str] = None
    password_hash: str = ""  # (bcrypt)
    roles: List[str] = None  # ID 
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_login: Optional[str] = None
    
    def __post_init__(self):
        """ """
        if self.roles is None:
            self.roles = []
    
    def to_dict(self) -> dict:
        """ ( )"""
        data = asdict(self)
        # password_hash 
        data.pop('password_hash', None)
        return data
    
    def to_dict_with_password(self) -> dict:
        """ ( , )"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'User':
        """ """
        # , roles None
        if 'roles' not in data or data['roles'] is None:
            data['roles'] = []
        return cls(**data)
    
    def has_role(self, role_id: str) -> bool:
        """, """
        return role_id in self.roles
    
    def add_role(self, role_id: str):
        """ """
        if role_id not in self.roles:
            self.roles.append(role_id)
    
    def remove_role(self, role_id: str):
        """ """
        if role_id in self.roles:
            self.roles.remove(role_id)
