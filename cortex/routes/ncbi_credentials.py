"""Authenticated settings endpoints for per-user NCBI credentials."""
from __future__ import annotations

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, HTTPException, Request

from cortex.ncbi_credentials import credential_status, delete_api_key, get_credential, save_credential
from cortex.schemas import NCBICredentialStatus, NCBICredentialUpdate

router = APIRouter(prefix="/user/ncbi-credentials", tags=["ncbi-credentials"])


@router.get("", response_model=NCBICredentialStatus)
async def get_ncbi_credential_status(request: Request) -> NCBICredentialStatus:
    return NCBICredentialStatus(**credential_status(get_credential(request.state.user.id)))


@router.put("", response_model=NCBICredentialStatus)
async def update_ncbi_credentials(
    request: Request, body: NCBICredentialUpdate
) -> NCBICredentialStatus:
    email = body.email.strip() if body.email else None
    if email:
        try:
            email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise HTTPException(status_code=422, detail="A valid NCBI contact email is required.") from exc
    try:
        credential = save_credential(request.state.user.id, email, body.api_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return NCBICredentialStatus(**credential_status(credential))


@router.delete("/api-key", response_model=NCBICredentialStatus)
async def remove_ncbi_api_key(request: Request) -> NCBICredentialStatus:
    delete_api_key(request.state.user.id)
    return NCBICredentialStatus(**credential_status(get_credential(request.state.user.id)))
