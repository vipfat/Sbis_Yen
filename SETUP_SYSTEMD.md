# Настройка автозапуска бота через systemd

## Инструкция для ручной настройки на сервере

Подключись к серверу и выполни эти команды:

```bash
# 1. Подключаемся к серверу
ssh bot@80.242.58.124

# 2. Останавливаем старый процесс (если запущен)
pkill -f bot_simple.py

# 3. Копируем service файл
sudo cp /tmp/sbis-bot.service /etc/systemd/system/sbis-bot.service

# 4. Применяем настройки systemd
sudo systemctl daemon-reload

# 5. Включаем автозапуск при загрузке
sudo systemctl enable sbis-bot

# 6. Запускаем сервис
sudo systemctl start sbis-bot

# 7. Проверяем статус (должно быть "active (running)")
sudo systemctl status sbis-bot

# 8. Смотрим последние логи
tail -f ~/bot_app/bot.log
```

## Полезные команды для управления ботом

```bash
# Проверить статус
sudo systemctl status sbis-bot

# Остановить бота
sudo systemctl stop sbis-bot

# Запустить бота
sudo systemctl start sbis-bot

# Перезапустить бота (например, после git pull)
sudo systemctl restart sbis-bot

# Посмотреть логи
tail -f ~/bot_app/bot.log

# Отключить автозапуск
sudo systemctl disable sbis-bot
```

## Что дает systemd:

✅ **Автозапуск** - бот запустится автоматически после перезагрузки сервера
✅ **Автоперезапуск** - если бот упадет с ошибкой, он перезапустится через 10 секунд
✅ **Логи** - все выводится в ~/bot_app/bot.log
✅ **Управление** - простые команды start/stop/restart

## Проверка что все работает:

```bash
# 1. Статус должен быть "active (running)"
sudo systemctl status sbis-bot

# 2. Процесс должен быть в списке
ps aux | grep bot_simple

# 3. В логах не должно быть ошибок
tail ~/bot_app/bot.log
```

## Обновление бота после изменений в коде:

```bash
cd ~/bot_app
git pull
sudo systemctl restart sbis-bot
```

Всё! Теперь бот будет работать надежно и автоматически.
