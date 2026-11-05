#!/usr/bin/env python3
import sys

paper_path = 'pumpdesk/paper.py'

try:
    with open(paper_path, 'r') as f:
        lines = f.readlines()
    
    added = False
    for i, line in enumerate(lines):
        if 'self.rules = rules' in line:
            if i+1 < len(lines) and 'self.account' in lines[i+1]:
                print('account already added in paper.py')
                added = True
                break
            lines.insert(i+1, '        self.account = self.rules.account\n')
            added = True
            print('Added self.account in paper.py')
            break
    
    if added:
        with open(paper_path, 'w') as f:
            f.writelines(lines)
        print('paper.py updated successfully')
    else:
        print('Could not find self.rules = rules in paper.py')
        sys.exit(1)
        
except Exception as e:
    print(f'Error: {e}')
    sys.exit(1)