from jarvis.control_center import EnvEditor, MASKED_SECRET_VALUE


def test_env_editor_masks_secret_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=secret-value\n"
        "TELEGRAM_BOT_TOKEN=telegram-token\n"
        "OPENAI_MODEL=gpt-4.1-mini\n",
        encoding="utf-8",
    )
    editor = EnvEditor(env_path)
    editor.example = tmp_path / ".env.example"

    values = editor.public_read()

    assert values["OPENAI_API_KEY"] == MASKED_SECRET_VALUE
    assert values["TELEGRAM_BOT_TOKEN"] == MASKED_SECRET_VALUE
    assert values["OPENAI_MODEL"] == "gpt-4.1-mini"
