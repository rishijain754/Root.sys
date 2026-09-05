import os
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
