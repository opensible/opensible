#!/usr/bin/env python3
"""
Helper 
"""
import logging
from typing import Optional, Dict, Any


def log_debug(logger: logging.Logger, message: str, context: Optional[Dict[str, Any]] = None):
    """
 DEBUG
    
    Args:
        logger: Logger instance
 message: 
 context: (execution_id, project_id, etc.)
    """
    if context:
        context_str = " | ".join([f"{k}={v}" for k, v in context.items()])
        logger.debug(f"{message} | {context_str}")
    else:
        logger.debug(message)


def log_info(logger: logging.Logger, message: str, context: Optional[Dict[str, Any]] = None):
    """
 INFO
    
    Args:
        logger: Logger instance
 message: 
 context: (execution_id, project_id, etc.)
    """
    if context:
        context_str = " | ".join([f"{k}={v}" for k, v in context.items()])
        logger.info(f"{message} | {context_str}")
    else:
        logger.info(message)


def log_warning(logger: logging.Logger, message: str, context: Optional[Dict[str, Any]] = None, exception: Optional[Exception] = None):
    """
 WARNING
    
    Args:
        logger: Logger instance
 message: 
 context: (execution_id, project_id, etc.)
 exception: 
    """
    if context:
        context_str = " | ".join([f"{k}={v}" for k, v in context.items()])
        full_message = f"{message} | {context_str}"
    else:
        full_message = message
    
    if exception:
        logger.warning(full_message, exc_info=exception)
    else:
        logger.warning(full_message)


def log_error(logger: logging.Logger, message: str, context: Optional[Dict[str, Any]] = None, exception: Optional[Exception] = None):
    """
 ERROR
    
    Args:
        logger: Logger instance
 message: 
 context: (execution_id, project_id, etc.)
 exception: 
    """
    if context:
        context_str = " | ".join([f"{k}={v}" for k, v in context.items()])
        full_message = f"{message} | {context_str}"
    else:
        full_message = message
    
    if exception:
        logger.error(full_message, exc_info=exception)
    else:
        logger.error(full_message)


def log_critical(logger: logging.Logger, message: str, context: Optional[Dict[str, Any]] = None, exception: Optional[Exception] = None):
    """
 CRITICAL
    
    Args:
        logger: Logger instance
 message: 
 context: (execution_id, project_id, etc.)
 exception: 
    """
    if context:
        context_str = " | ".join([f"{k}={v}" for k, v in context.items()])
        full_message = f"{message} | {context_str}"
    else:
        full_message = message
    
    if exception:
        logger.critical(full_message, exc_info=exception)
    else:
        logger.critical(full_message)
