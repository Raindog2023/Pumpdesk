#!/usr/bin/env python3
import sys

live_path = 'pumpdesk/live.py'

try:
    with open(live_path, 'r') as f:
        lines = f.readlines()
    
    added = False
    for i, line in enumerate(lines):
        if 'self.rules = rules' in line:
            # Check if account already added
            if i+1 < len(lines) and 'self.account' in lines[i+1]:
                print('account already added in live.py')
                added = True
                break
            # Add after self.rules = rules
            lines.insert(i+1, '        self.account = self.rules.account\n')
            added = True
            print(f'Added self.account at line {i+2} in live.py')
            break
    
    if added:
        with open(live_path, 'w') as f:
            f.writelines(lines)
        print('live.py updated successfully')
    else:
        print('Could not find self.rules = rules in live.py')
        sys.exit(1)
        
except Exception as e:
    print(f'Error: {e}')
    sys.exit(1)