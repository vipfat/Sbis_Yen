"""
Централизованная конфигурация для SBIS Telegram Bot.
Все константы и настройки в одном месте.
"""
import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class CompanyConfig:
    """Реквизиты компании"""
    inn: str = "940200200247"
    name: str = "Плетнев Сергей Юрьевич, ИП, Город Севастополь"
    warehouse_name: str = "ИП Плетнев"
    warehouse_id: str = "284"
    recipient_name: str = "Фирлесс, ООО"
    recipient_inn: str = "7710000001"
    account: str = "20-01"
    writeoff_purpose: str = "Списание материально-производственных запасов на затраты"


@dataclass
class PathsConfig:
    """Пути к файлам"""
    catalog_excel: str = "Каталог.xlsx"
    compositions_excel: str = "Реестр составов.xlsx"
    production_excel: str = "Производство.xlsx"
    tmp_images_dir: str = "tmp_images"
    logs_dir: str = "logs"
    ocr_log: str = "logs/ocr_tables.log"
    catalog_log: str = "logs/catalog_matching.log"


@dataclass
class APIConfig:
    """API ключи и токены"""
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    sbis_id_connect: str = os.getenv("ID_Connect", "")
    sbis_protect_key: str = os.getenv("Protect_key", "")
    sbis_service_key: str = os.getenv("Service_key", "")


@dataclass
class TimeoutConfig:
    """Таймауты для запросов"""
    telegram_polling: int = 30
    telegram_file_download: int = 60
    telegram_api_request: int = 35
    openai_request: int = 120
    sbis_request: int = 35


@dataclass
class AIConfig:
    """Настройки AI/ML"""
    openai_model: str = "gpt-4o"
    whisper_model: str = "whisper-1"
    min_similarity_score: float = 0.5
    similarity_weights: dict = None
    
    def __post_init__(self):
        if self.similarity_weights is None:
            self.similarity_weights = {
                "sequence_matcher": 0.40,
                "token_overlap": 0.25,
                "levenshtein": 0.35
            }


@dataclass
class ValidationConfig:
    """Правила валидации"""
    valid_inn_lengths: tuple = (10, 12)
    date_format: str = "%d.%m.%Y"
    max_file_size_mb: int = 20


# Singleton экземпляры конфигураций
COMPANY = CompanyConfig()
PATHS = PathsConfig()
API = APIConfig()
TIMEOUTS = TimeoutConfig()
AI = AIConfig()
VALIDATION = ValidationConfig()


# Константы для типов документов
DOC_TYPE_LABELS = {
    "production": "Производство",
    "writeoff": "Списание",
    "income": "Приход",
}


# Специальные маппинги для исправления путаницы
SPECIAL_NAME_MAPPINGS = {
    # "хот" без "соус" = колбаски охотничьи
    "forced_mappings": [
        {
            "triggers": ["хот"],
            "exclude_triggers": ["соус"],
            "result": "КОЛБАСКИ ОХОТНИЧЬИ"
        }
    ]
}


def validate_config() -> tuple[bool, Optional[str]]:
    """
    Проверяет наличие всех обязательных настроек.
    
    Returns:
        tuple: (is_valid, error_message)
    """
    if not API.telegram_bot_token:
        return False, "TELEGRAM_BOT_TOKEN не задан в .env"
    
    if not API.openai_api_key:
        return False, "OPENAI_API_KEY не задан в .env"
    
    if not API.sbis_id_connect or not API.sbis_protect_key or not API.sbis_service_key:
        return False, "SBIS credentials (ID_Connect, Protect_key, Service_key) не заданы в .env"
    
    return True, None


if __name__ == "__main__":
    # Тест конфигурации
    is_valid, error = validate_config()
    if is_valid:
        print("✅ Конфигурация валидна")
        print(f"Компания: {COMPANY.name}")
        print(f"Склад: {COMPANY.warehouse_name}")
        print(f"Telegram Bot: {'***' + API.telegram_bot_token[-4:] if API.telegram_bot_token else 'НЕ ЗАДАН'}")
        print(f"OpenAI: {'***' + API.openai_api_key[-4:] if API.openai_api_key else 'НЕ ЗАДАН'}")
    else:
        print(f"❌ Ошибка конфигурации: {error}")
