#!/usr/bin/env python3
import sys

feed_path = 'pumpdesk/feed.py'

try:
    with open(feed_path, 'r') as f:
        content = f.read()
    
    # Check if import already exists
    if 'from .decoder import events_from_notification' in content:
        print('Import already exists in feed.py')
        sys.exit(0)
    
    # Add the import after 'import websockets'
    if 'import websockets' in content:
        new_content = content.replace(
            'import websockets',
            'import websockets\nfrom .decoder import events_from_notification'
        )
        with open(feed_path, 'w') as f:
            f.write(new_content)
        print('Import added successfully to feed.py')
    else:
        print('Could not find import websockets line in feed.py')
        sys.exit(1)
        
except Exception as e:
    print(f'Error: {e}')
    sys.exit(1)
#!/usr/bin/env python3
import sys

feed_path = 'pumpdesk/feed.py'

try:
    with open(feed_path, 'r') as f:
        content = f.read()
    
    # Check if import already exists
    if 'from .decoder import events_from_notification' in content:
        print('Import already exists in feed.py')
        sys.exit(0)
    
    # Add the import after 'import websockets'
    if 'import websockets' in content:
        new_content = content.replace(
            'import websockets',
            'import websockets\nfrom .decoder import events_from_notification'
        )
        with open(feed_path, 'w') as f:
            f.write(new_content)
        print('Import added successfully to feed.py')
    else:
        print('Could not find import websockets line in feed.py')
        sys.exit(1)
        
except Exception as e:
    print(f'Error: {e}')
    sys.exit(1)