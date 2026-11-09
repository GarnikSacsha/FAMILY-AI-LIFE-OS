from fastapi.testclient import TestClient

from app.api.application import create_application


def test_public_pages_are_available_without_sign_in():
    client = TestClient(create_application(start_telegram=False))
    for path, title in [("/", "FamilyBotapp"), ("/privacy", "Политика конфиденциальности")]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert title in response.text
        assert 'lang="ru"' in response.text
        assert 'name="viewport"' in response.text
        assert 'href="/privacy"' in response.text
        assert "ssobachkovna@gmail.com" in response.text
        assert "<script" not in response.text


def test_privacy_discloses_google_data_processors_and_deletion():
    client = TestClient(create_application(start_telegram=False))
    text = client.get("/privacy").text
    for disclosure in [
        "Gmail",
        "Calendar",
        "OpenAI",
        "Google Gemini",
        "Railway",
        "Telegram",
        "удаление",
        "Limited Use",
    ]:
        assert disclosure in text
    assert 'href="https://myaccount.google.com/connections"' in text
