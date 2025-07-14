import random
import os
from collections import deque
from dotenv import load_dotenv
from telebot import types, TeleBot, custom_filters
from telebot.storage import StateMemoryStorage
from telebot.handler_backends import State, StatesGroup
import db

load_dotenv()

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
state_storage = StateMemoryStorage()
bot = TeleBot(TOKEN, state_storage=state_storage, parse_mode='HTML')


class Command:
    ADD_WORD = 'Добавить слово ➕'
    DELETE_WORD = 'Удалить слово ➖'
    NEXT = 'Дальше ▶'
    STATS = 'Статистика 📊'
    ACHIEVEMENTS = 'Достижения 🏆'
    SETTINGS = '⚙️ Режим'

ACHIEVEMENTS_MAP = {
    'learned_10': '🎓 Новичок - Выучено 10 слов.',
    'learned_50': '🧐 Знаток - Выучено 50 слов.',
    'learned_100': '🧠 Полиглот - Выучено 100 слов.',
    'streak_3': '🔥 Упорство - Серия 3 дня.',
    'streak_7': '🚀 Марафонец - Серия 7 дней.',
    'streak_14': '🏆 Чемпион - Серия 14 дней.',
    'first_word': '✍️ Первопроходец - Добавлено первое личное слово.',
}

# {user_id: {'review_queue': deque([...]), 'review_countdown': 0}}
user_session = {}


class MyStates(StatesGroup):
    target_word = State()
    translate_word = State()
    options = State()
    add_word = State()
    delete_word = State()


def init_db():
    print("Initializing database...")
    db.create_tables()
    # Если в базе нет слов, импортируем из файла
    if not db.get_common_words():
        print("Database is empty. Populating with initial words from 5000_words.txt...")
        print("This may take a moment, please wait...")
        db.import_words_from_txt('5000_words.txt')
        print("Database populated successfully.")
    print("Database is ready.")


def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton(Command.NEXT))
    markup.add(
        types.KeyboardButton(Command.ADD_WORD),
        types.KeyboardButton(Command.DELETE_WORD),
        types.KeyboardButton(Command.STATS)
    )
    markup.add(
        types.KeyboardButton(Command.ACHIEVEMENTS),
        types.KeyboardButton(Command.SETTINGS)
    )
    return markup


def get_options_keyboard(options):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    # Сначала добавляем варианты ответа
    for opt in options:
        markup.add(types.KeyboardButton(opt))
    # Затем добавляем главные кнопки
    markup.add(
        types.KeyboardButton(Command.ADD_WORD),
        types.KeyboardButton(Command.DELETE_WORD),
        types.KeyboardButton(Command.STATS)
    )
    markup.add(
        types.KeyboardButton(Command.ACHIEVEMENTS),
        types.KeyboardButton(Command.SETTINGS)
    )
    markup.add(types.KeyboardButton(Command.NEXT))
    return markup


def check_and_grant_achievements(user_id, chat_id):
    stats = db.get_user_stats_for_achievements(user_id)
    if not stats:
        return

    user_achievements = db.get_user_achievements(user_id)
    
    def grant_if_not_present(ach_id, condition):
        if condition and ach_id not in user_achievements:
            db.grant_achievement(user_id, ach_id)
            bot.send_message(chat_id, f"🎉 <b>Новое достижение!</b>\n{ACHIEVEMENTS_MAP[ach_id]}", reply_markup=get_main_keyboard())

    grant_if_not_present('learned_10', stats['learned_count'] >= 10)
    grant_if_not_present('learned_50', stats['learned_count'] >= 50)
    grant_if_not_present('learned_100', stats['learned_count'] >= 100)
    grant_if_not_present('streak_3', stats['current_streak'] >= 3)
    grant_if_not_present('streak_7', stats['current_streak'] >= 7)
    grant_if_not_present('streak_14', stats['current_streak'] >= 14)
    grant_if_not_present('first_word', stats['personal_words_count'] >= 1)


@bot.message_handler(commands=['start'])
def start_handler(message):
    telegram_id = message.from_user.id
    username = message.from_user.username
    db.register_user(telegram_id, username)
    greeting = (
        'Привет 👋 Давай попрактикуемся в английском языке.\n'
        'Нажми <b>Дальше ▶</b>, чтобы начать тренировку.'
    )
    bot.send_message(message.chat.id, greeting, reply_markup=get_main_keyboard())
    # Обновляем серию и проверяем ачивки
    user_id = db.get_user_id(telegram_id)
    if user_id:
        db.update_user_streak(user_id)
        check_and_grant_achievements(user_id, message.chat.id)


@bot.message_handler(func=lambda m: m.text == Command.NEXT)
def next_question_handler(message):
    telegram_id = message.from_user.id
    user_id = db.get_user_id(telegram_id)
    session = user_session.setdefault(telegram_id, {'review_queue': deque(), 'review_countdown': 0})
    
    correct_pair = None
    options_en = []
    
    # Приоритет - слова на повторение
    is_review_time = session['review_queue'] and session['review_countdown'] <= 0
    if is_review_time:
        correct_pair = session['review_queue'].popleft()
        distractors = db.get_distractors(user_id, correct_pair[0])
        
        if len(distractors) == 3:
            all_pairs = [correct_pair] + distractors
            options_en = [p[0] for p in all_pairs]
            random.shuffle(options_en)
        else:
            # Если не удалось найти 3 других слова, возвращаем слово в очередь и берем случайное
            session['review_queue'].appendleft(correct_pair)
            correct_pair = None # Сбрасываем, чтобы перейти к обычному выбору
    
    # Обычный выбор слова, если не было слова на повторение
    if correct_pair is None:
        words = db.get_random_words_for_user(user_id)
        if len(words) < 4:
            bot.send_message(message.chat.id, 'Недостаточно слов для тренировки. Добавьте еще!', reply_markup=get_main_keyboard())
            return
        
        correct_pair = words[0]
        options_en = [pair[0] for pair in words]
        random.shuffle(options_en)
        
        # Уменьшаем счетчик до следующего повтора
        session['review_countdown'] = max(0, session['review_countdown'] - 1)

    training_mode = db.get_user_training_mode(user_id)
    if training_mode == 'ru_en':
        question_word, answer_word = correct_pair[1], correct_pair[0]
        options = options_en
    else: # en_ru
        question_word, answer_word = correct_pair[0], correct_pair[1]
        # Для режима EN-RU нужны русские варианты
        distractors = db.get_distractors(user_id, correct_pair[0])
        options = [answer_word] + [d[1] for d in distractors]
        random.shuffle(options)

    bot.set_state(telegram_id, MyStates.target_word, message.chat.id)
    with bot.retrieve_data(telegram_id, message.chat.id) as data:
        data['word_en'] = correct_pair[0] # Всегда храним EN
        data['word_ru'] = correct_pair[1] # Всегда храним RU
        data['target_word'] = answer_word
        data['translate_word'] = question_word
        data['options'] = options

    bot.send_message(
        message.chat.id,
        f'Как переводится: <b>{question_word}</b>?',
        reply_markup=get_options_keyboard(options)
    )


@bot.message_handler(func=lambda m: m.text == Command.STATS)
def stats_handler(message):
    telegram_id = message.from_user.id
    user_id = db.get_user_id(telegram_id)

    # Обновляем серию перед показом статистики
    current_streak = db.update_user_streak(user_id)
    check_and_grant_achievements(user_id, message.chat.id)
    
    common_count = db.count_common_words()
    user_count = db.count_user_words(user_id)
    learned_count = db.get_today_correct_answers(user_id)
    total_unique = common_count + user_count
    current_mode = "🇷🇺 Русский -> 🇬🇧 Английский" if db.get_user_training_mode(user_id) == 'ru_en' else "🇬🇧 Английский -> 🇷🇺 Русский"

    stats_text = (
        f"📊 <b>Ваша статистика</b>\n\n"
        f"🔥 Ежедневная серия: <b>{current_streak}</b>\n"
        f"✅ Правильных ответов сегодня: <b>{learned_count}</b>\n\n"
        f"⚙️ Текущий режим: {current_mode}\n\n"
        f"📖 <b>Словарный запас:</b>\n"
        f"- Общий словарь: <b>{common_count}</b> слов\n"
        f"- Ваши личные слова: <b>{user_count}</b> слов\n"
        f"- Всего для изучения: <b>{total_unique}</b> слов\n\n"
        f'Нажмите "{Command.NEXT}", чтобы продолжить тренировку!'
    )
    
    bot.send_message(message.chat.id, stats_text, reply_markup=get_main_keyboard())
    # Сбрасываем состояние, если пользователь был в процессе ответа на вопрос
    bot.delete_state(telegram_id, message.chat.id)


@bot.message_handler(func=lambda m: m.text == Command.ADD_WORD)
def add_word_handler(message):
    bot.send_message(message.chat.id, 'Введите слово в формате: <b>english - русский</b>')
    bot.set_state(message.from_user.id, MyStates.add_word, message.chat.id)


@bot.message_handler(state=MyStates.add_word, content_types=['text'])
def save_new_word(message):
    telegram_id = message.from_user.id
    user_id = db.get_user_id(telegram_id)
    try:
        en, ru = [s.strip() for s in message.text.split('-', 1)]
        db.add_user_word(user_id, en, ru)
        bot.send_message(message.chat.id, f'Слово <b>"{en}"</b> добавлено!', reply_markup=get_main_keyboard())
        check_and_grant_achievements(user_id, message.chat.id)
    except ValueError:
        bot.send_message(message.chat.id, 'Ошибка! Введите слово в формате: <b>english - русский</b>', reply_markup=get_main_keyboard())
    
    bot.delete_state(message.from_user.id, message.chat.id)


@bot.message_handler(func=lambda m: m.text == Command.DELETE_WORD)
def delete_word_handler(message):
    bot.send_message(message.chat.id, 'Введите английское слово, которое хотите удалить')
    bot.set_state(message.from_user.id, MyStates.delete_word, message.chat.id)


@bot.message_handler(state=MyStates.delete_word, content_types=['text'])
def delete_word_confirm(message):
    telegram_id = message.from_user.id
    user_id = db.get_user_id(telegram_id)
    word_en = message.text.strip()
    db.delete_user_word(user_id, word_en)
    bot.send_message(message.chat.id, f'Слово <b>"{word_en}"</b> удалено (если оно было в вашей базе).', reply_markup=get_main_keyboard())
    bot.delete_state(message.from_user.id, message.chat.id)


@bot.message_handler(state=MyStates.target_word, content_types=['text'])
def answer_handler(message):
    # Этот обработчик теперь срабатывает только в состоянии вопроса
    telegram_id = message.from_user.id

    # Сначала проверяем, не нажал ли пользователь на команду
    if message.text in [Command.STATS, Command.ACHIEVEMENTS, Command.SETTINGS, Command.ADD_WORD, Command.DELETE_WORD]:
        bot.delete_state(telegram_id, message.chat.id)
        # Имитируем, что команду вызвал сам пользователь
        bot.process_new_messages([message])
        return

    with bot.retrieve_data(telegram_id, message.chat.id) as data:
        target_word = data.get('target_word')
        word_en = data.get('word_en')
        word_ru = data.get('word_ru')
        options = data.get('options')

    if not target_word or message.text not in options:
        # Игнорируем, если пришел текст не из кнопок-вариантов
        return

    if message.text == target_word:
        bot.send_message(message.chat.id, '<b>Правильно! 👍</b>')
        # Обновляем прогресс
        user_id = db.get_user_id(telegram_id)
        db.log_correct_answer(user_id)
        db.update_user_streak(user_id)
        check_and_grant_achievements(user_id, message.chat.id)
    else:
        bot.send_message(message.chat.id, f'Неправильно. Правильный ответ: <b>{data.get("translate_word")}</b> -> <b>{target_word}</b>')
        # Добавляем слово в очередь на повторение
        session = user_session.setdefault(telegram_id, {'review_queue': deque(), 'review_countdown': 0})
        word_pair = (word_en, word_ru)
        if word_pair not in session['review_queue']:
            session['review_queue'].append(word_pair)
        
        # Если счетчик не активен, устанавливаем его
        if session['review_countdown'] <= 0:
            session['review_countdown'] = random.randint(3, 5)
    
    bot.delete_state(telegram_id, message.chat.id)
    next_question_handler(message)


@bot.message_handler(func=lambda m: m.text == Command.ACHIEVEMENTS)
def achievements_handler(message):
    telegram_id = message.from_user.id
    user_id = db.get_user_id(telegram_id)
    user_achievements = db.get_user_achievements(user_id)

    if not user_achievements:
        ach_text = "🏆 <b>Ваши достижения</b>\n\nУ вас пока нет достижений. Продолжайте заниматься, и они обязательно появятся!"
    else:
        ach_text = "🏆 <b>Ваши достижения</b>\n\n"
        for ach_id in ACHIEVEMENTS_MAP:
            if ach_id in user_achievements:
                ach_text += f"✅ {ACHIEVEMENTS_MAP[ach_id]}\n"
            else:
                ach_text += f"❌ {ACHIEVEMENTS_MAP[ach_id]}\n"

    bot.send_message(message.chat.id, ach_text, reply_markup=get_main_keyboard())


@bot.message_handler(func=lambda m: m.text == Command.SETTINGS)
def settings_handler(message):
    user_id = db.get_user_id(message.from_user.id)
    current_mode = db.get_user_training_mode(user_id)
    mode_text = "🇷🇺 Русский -> 🇬🇧 Английский" if current_mode == 'ru_en' else "🇬🇧 Английский -> 🇷🇺 Русский"

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🇷🇺 -> 🇬🇧", callback_data="set_mode:ru_en"),
        types.InlineKeyboardButton("🇬🇧 -> 🇷🇺", callback_data="set_mode:en_ru")
    )
    bot.send_message(message.chat.id, f"Ваш текущий режим: <b>{mode_text}</b>\n\nВыберите новый:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('set_mode:'))
def set_mode_callback(call):
    mode = call.data.split(':')[1]
    telegram_id = call.from_user.id
    user_id = db.get_user_id(telegram_id)
    db.set_user_training_mode(user_id, mode)

    mode_text = "Русский -> Английский" if mode == 'ru_en' else "Английский -> Русский"
    bot.answer_callback_query(call.id, f"✅ Режим изменен на: <b>{mode_text}</b>")
    bot.edit_message_text(f"✅ Режим изменен на: <b>{mode_text}</b>", call.message.chat.id, call.message.message_id, reply_markup=None)
    # Не вызываем следующий вопрос автоматом, даем пользователю нажать "Дальше"
    bot.send_message(call.message.chat.id, 'Нажмите "Дальше ▶", чтобы начать тренировку в новом режиме.', reply_markup=get_main_keyboard())


bot.add_custom_filter(custom_filters.StateFilter(bot))

if __name__ == '__main__':
    init_db()
    print("Bot is starting...")
    bot.infinity_polling(skip_pending=True) 