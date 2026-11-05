with open('pumpdesk/engine.py', 'r') as f:
    content = f.read()

# Fix both account.get() calls
content = content.replace('account.get("equity", 0)', 'getattr(account, "equity", 0)')
content = content.replace('account.get("cash", 0)', 'getattr(account, "cash", 0)')

with open('pumpdesk/engine.py', 'w') as f:
    f.write(content)

print('Fixed engine.py')