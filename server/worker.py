#!/usr/bin/env python3
"""
Worker playbook runs
Thin-wrapper - worker.main
"""
import sys
from pathlib import Path

# web worker 
web_dir = Path(__file__).parent.parent
sys.path.insert(0, str(web_dir))

# worker 
if __name__ == '__main__':
    from worker.main import main
    main()
