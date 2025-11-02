from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from auth_utils import users_collection, verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])

class UserLogin(BaseModel):
    username: str
    password: str

@router.post("/login")
async def login(creds: UserLogin):
    col = users_collection()
    doc = col.find_one({"username": creds.username})
    if not doc or not verify_password(creds.password, doc["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad credentials")
    token = create_access_token(username=doc["username"], role=doc["role"])
    return {"access_token": token, "token_type": "bearer", "role": doc["role"]}