from collections.abc import AsyncIterator
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.application.ports.asset_repository import AssetRepository
from app.application.ports.media_verifier import MediaVerifierPort
from app.application.ports.object_storage import ObjectStoragePort
from app.application.services.asset_store import AssetStore
from app.domain.user import User
from app.infrastructure.documents.document_verifier import DocumentVerifier
from app.main import create_app
from app.settings import Settings
from tests.support.fakes import FakeTokenVerifier, FakeUserService


def create_test_client(
    user: User,
    repo: AssetRepository,
    storage: ObjectStoragePort,
    media_verifier: MediaVerifierPort | None = None,
) -> AsyncClient:
    settings = Settings(
        app_env="test",
        oidc_issuer="https://auth.example",
        oidc_audience="test",
        oidc_jwks_url="https://auth.example/.well-known/jwks.json",
        object_storage_endpoint="http://127.0.0.1:9000",
        object_storage_bucket="virtujudge",
    )
    application = create_app(settings)
    application.state.token_verifier = FakeTokenVerifier(user)
    application.state.user_service_factory = lambda session: FakeUserService(user)

    asset_store = AssetStore(
        repository=repo,
        storage=storage,
        document_verifier=DocumentVerifier(),
        media_verifier=media_verifier,
        upload_ttl_seconds=900,
        download_ttl_seconds=900,
    )
    application.state.asset_store_factory = lambda session: asset_store

    async def mock_session(request: Any = None) -> AsyncIterator[None]:
        yield None

    application.state.session_dependency = mock_session

    transport = ASGITransport(app=application)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer fake-token"},
    )
