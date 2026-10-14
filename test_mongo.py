
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

uri = os.getenv("MONGO_URI")
db_name = os.getenv("MONGO_DB")

if not uri or not db_name:
    raise ValueError("MONGO_URI or MONGO_DB is missing from .env")

client = MongoClient(uri, serverSelectionTimeoutMS=10000)

try:
    client.admin.command("ping")
    print("MongoDB connection successful!")
    print("Database:", db_name)
except Exception as exc:
    print("MongoDB connection failed:", exc)
finally:
    client.close()
