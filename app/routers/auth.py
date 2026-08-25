from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from sqlalchemy import select

from app.database import async_session_maker
from app.middleware.auth import create_jwt
from app.models.user import User

router = APIRouter(prefix='/auth', tags=['auth'])
pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=6, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=255)


@router.post('/register')
async def register(payload: RegisterRequest):
    async with async_session_maker() as session:
        existing = await session.execute(select(User).where(User.email == payload.email.lower()))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=400, detail={'error': 'USER_EXISTS', 'message': 'User already exists'})

        user = User(
            email=payload.email.lower(),
            name=payload.name.strip(),
            password_hash=pwd_context.hash(payload.password),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return {'user_id': str(user.id), 'email': user.email, 'name': user.name}


@router.post('/login')
async def login(payload: LoginRequest):
    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.email == payload.email.lower()))
        if user is None or not pwd_context.verify(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail={'error': 'INVALID_CREDENTIALS', 'message': 'Invalid credentials'})
        token = create_jwt(str(user.id))
        return {'access_token': token, 'token_type': 'bearer'}
