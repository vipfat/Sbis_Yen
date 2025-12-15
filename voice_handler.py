# voice_handler.py
"""
Улучшенный модуль для обработки голосового ввода через Whisper API.
"""

import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

_OPENAI_CLIENT = None


def _get_openai_client() -> OpenAI:
    """Ленивая инициализация OpenAI клиента."""
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY не установлен в .env")
        _OPENAI_CLIENT = OpenAI(api_key=api_key)
    return _OPENAI_CLIENT


def transcribe_audio(file_path: str, language: str = "ru") -> str:
    """
    Преобразует аудиофайл в текст через Whisper API.
    
    Args:
        file_path: Путь к аудиофайлу
        language: Язык аудио (по умолчанию "ru")
        
    Returns:
        Распознанный текст
        
    Raises:
        RuntimeError: Если не удалось распознать речь
    """
    client = _get_openai_client()
    
    if not Path(file_path).exists():
        raise FileNotFoundError(f"Аудиофайл не найден: {file_path}")
    
    try:
        with open(file_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=language,
                prompt="Список продуктов для кафе с названиями и количествами"  # Контекст для лучшего распознавания
            )
        
        text = getattr(result, "text", None)
        if isinstance(result, dict):
            text = text or result.get("text")
        
        if not text:
            raise RuntimeError("Whisper вернул пустой результат")
        
        return text.strip()
        
    except Exception as e:
        raise RuntimeError(f"Ошибка распознавания речи: {e}") from e


def enhance_transcription_with_gpt(raw_text: str, context: str = "кафе") -> str:
    """
    Улучшает распознанный текст через GPT: исправляет ошибки, нормализует формат.
    
    Args:
        raw_text: Сырой текст от Whisper
        context: Контекст для лучшего понимания ("кафе", "склад" и т.д.)
        
    Returns:
        Улучшенный текст
    """
    client = _get_openai_client()
    
    prompt = f"""Ты — система обработки голосового ввода для {context}.

Исходный текст от голосового распознавания:
{raw_text}

Задачи:
1. Исправь ошибки распознавания (неправильно услышанные слова)
2. Нормализуй названия продуктов (заглавные буквы)
3. Убедись что числа правильно записаны (дробные через точку)
4. Сохрани формат "Название Количество"

Верни ТОЛЬКО исправленный текст без пояснений."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=500
        )
        
        enhanced = response.choices[0].message.content.strip()
        return enhanced if enhanced else raw_text
        
    except Exception:
        # Если GPT недоступен, возвращаем оригинал
        return raw_text
