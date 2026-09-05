import os

root_dir = r'c:\Users\Rishi\Documents\GitHub\Root.sys'
api_dir = os.path.join(root_dir, 'api')
os.makedirs(api_dir, exist_ok=True)

# 1. api/index.py
api_index_content = """import os
import sys

# Ensure backend directory is on sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
backend_dir = os.path.join(root_dir, "backend")

if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from api_server import FintechAPIServer

# Vercel Python runtime uses handler (BaseHTTPRequestHandler subclass)
class handler(FintechAPIServer):
    pass
"""

with open(os.path.join(api_dir, 'index.py'), 'w', encoding='utf-8') as f:
    f.write(api_index_content)

print('Created api/index.py')

# 2. vercel.json
vercel_json_content = """{
  "version": 2,
  "builds": [
    {
      "src": "api/index.py",
      "use": "@vercel/python"
    },
    {
      "src": "frontend/**",
      "use": "@vercel/static"
    }
  ],
  "routes": [
    {
      "src": "/api/(.*)",
      "dest": "/api/index.py"
    },
    {
      "src": "/docs",
      "dest": "/api/index.py"
    },
    {
      "src": "/",
      "dest": "/frontend/index.html"
    },
    {
      "src": "/(.*)",
      "dest": "/frontend/"
    }
  ]
}
"""

with open(os.path.join(root_dir, 'vercel.json'), 'w', encoding='utf-8') as f:
    f.write(vercel_json_content)

print('Created vercel.json')

# 3. requirements.txt
requirements_content = """# Standard library modules only - optional dependencies below
# google-genai
"""

with open(os.path.join(root_dir, 'requirements.txt'), 'w', encoding='utf-8') as f:
    f.write(requirements_content)

print('Created requirements.txt')

# 4. .vercelignore
vercelignore_content = """.git
.vscode
__pycache__
*.pyc
start.bat
verify_fixes.py
find_orders.py
build_app.py
setup_vercel.py
"""

with open(os.path.join(root_dir, '.vercelignore'), 'w', encoding='utf-8') as f:
    f.write(vercelignore_content)

print('Created .vercelignore')
