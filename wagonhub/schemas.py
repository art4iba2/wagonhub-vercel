"""Validation for public API payloads."""
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, EmailStr, Field, field_validator


def clean(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Поле не может быть пустым")
    return value


class Register(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Literal['track', 'wagon']

    @field_validator('name')
    @classmethod
    def name_clean(cls, v):
        return clean(v)


class Login(BaseModel):
    email: EmailStr
    password: str


class CompanyIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    inn: str = Field(pattern=r'^(?:\d{10}|\d{12})$')
    email: EmailStr
    phone: str = Field(default='', max_length=30)

    @field_validator('name')
    @classmethod
    def name_clean(cls, v):
        return clean(v)


class ListingIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    city: str = Field(min_length=2, max_length=100)
    address: str = Field(min_length=3, max_length=200)
    capacity: int = Field(gt=0, le=100000)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    description: str = Field(default='', max_length=3000)

    @field_validator('title', 'city', 'address')
    @classmethod
    def required_clean(cls, v):
        return clean(v)


class DealIn(BaseModel):
    listing_id: int = Field(gt=0)
    wagons: int = Field(gt=0, le=100000)


class Decision(BaseModel):
    status: Literal['confirmed', 'rejected']
