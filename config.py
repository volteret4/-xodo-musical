import os

COLLECTION_1_PATH = os.environ.get("COLLECTION_1_PATH", "/mnt/music/coleccion1")
COLLECTION_2_PATH = os.environ.get("COLLECTION_2_PATH", "/mnt/music/coleccion2")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music.db")
FLASK_HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.environ.get("FLASK_PORT", 5000))
