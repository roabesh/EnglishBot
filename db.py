import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import os
from datetime import date, timedelta

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'englishbot')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')


@contextmanager
def get_conn():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    try:
        yield conn
    finally:
        conn.close()


def create_tables():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                training_mode TEXT DEFAULT 'ru_en',
                current_streak INTEGER DEFAULT 0,
                last_seen_date DATE
            );
            CREATE TABLE IF NOT EXISTS words (
                id SERIAL PRIMARY KEY,
                word_en TEXT NOT NULL,
                word_ru TEXT NOT NULL,
                UNIQUE (word_en, word_ru)
            );
            CREATE TABLE IF NOT EXISTS user_words (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                word_en TEXT NOT NULL,
                word_ru TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_user_progress (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                progress_date DATE NOT NULL,
                correct_answers INTEGER DEFAULT 0,
                UNIQUE (user_id, progress_date)
            );
            CREATE TABLE IF NOT EXISTS user_achievements (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                achievement_id TEXT NOT NULL,
                achieved_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE(user_id, achievement_id)
            );
            ''')
            conn.commit()


def register_user(telegram_id, username=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO users (telegram_id, username, last_seen_date)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO NOTHING;
            ''', (telegram_id, username, date.today()))
            conn.commit()


def get_user_id(telegram_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM users WHERE telegram_id = %s', (telegram_id,))
            result = cur.fetchone()
            return result[0] if result else None


def count_common_words():
    """Возвращает количество слов в общей таблице `words`."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM words')
            result = cur.fetchone()
            return result[0] if result else 0


def count_user_words(user_id):
    """Возвращает количество личных слов пользователя."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM user_words WHERE user_id = %s', (user_id,))
            result = cur.fetchone()
            return result[0] if result else 0


def get_user_training_mode(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT training_mode FROM users WHERE id = %s', (user_id,))
            row = cur.fetchone()
            return row[0] if row else 'ru_en'

def set_user_training_mode(user_id, mode):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE users SET training_mode = %s WHERE id = %s', (mode, user_id))
            conn.commit()

def update_user_streak(user_id):
    """
    Обновляет ежедневную серию пользователя.
    Возвращает текущую серию.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT current_streak, last_seen_date FROM users WHERE id = %s', (user_id,))
            row = cur.fetchone()
            if not row:
                return 0

            current_streak, last_seen_date = row
            today = date.today()

            if last_seen_date == today: # Уже заходил сегодня
                if current_streak == 0:
                    current_streak = 1
            elif last_seen_date == today - timedelta(days=1): # Заходил вчера
                current_streak += 1
            else: # Пропустил день или первый раз заходит
                current_streak = 1
            
            cur.execute(
                'UPDATE users SET current_streak = %s, last_seen_date = %s WHERE id = %s',
                (current_streak, today, user_id)
            )
            conn.commit()
            return current_streak

def get_user_stats_for_achievements(user_id):
    """Возвращает статистику пользователя для проверки достижений."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''
                SELECT 
                    (SELECT COALESCE(SUM(correct_answers), 0) FROM daily_user_progress WHERE user_id = u.id) as learned_count,
                    (SELECT COUNT(*) FROM user_words WHERE user_id = u.id) as personal_words_count,
                    u.current_streak
                FROM users u
                WHERE u.id = %s;
            ''', (user_id,))
            return cur.fetchone()

def grant_achievement(user_id, achievement_id):
    """Присваивает пользователю достижение."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO user_achievements (user_id, achievement_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING;
            ''', (user_id, achievement_id))
            conn.commit()


def get_user_achievements(user_id):
    """Возвращает список ID достижений пользователя."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT achievement_id FROM user_achievements WHERE user_id = %s', (user_id,))
            return [row[0] for row in cur.fetchall()]


def add_word(word_en, word_ru):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO words (word_en, word_ru) VALUES (%s, %s)
                ON CONFLICT (word_en, word_ru) DO NOTHING;
            ''', (word_en, word_ru))
            conn.commit()


def get_common_words():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT word_en, word_ru FROM words')
            return cur.fetchall()


def add_user_word(user_id, word_en, word_ru):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO user_words (user_id, word_en, word_ru) VALUES (%s, %s, %s)
            ''', (user_id, word_en, word_ru))
            conn.commit()


def get_user_words(user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT word_en, word_ru FROM user_words WHERE user_id = %s', (user_id,))
            return cur.fetchall()


def delete_user_word(user_id, word_en):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM user_words WHERE user_id = %s AND word_en = %s', (user_id, word_en))
            conn.commit()


def import_words_from_txt(filepath):
    """Импортирует слова из файла формата '"word";"перевод"' в таблицу words."""
    with open(filepath, encoding='utf-8') as f:
        i = 0
        for line in f:
            if not line.strip():
                continue
            try:
                parts = line.strip().split(';')
                if len(parts) != 2:
                    continue
                word_en = parts[0].strip('"')
                word_ru = parts[1].strip('"')
                add_word(word_en, word_ru)
                i += 1
                if i % 1000 == 0:
                    print(f"  ... {i} words imported.")
            except Exception:
                continue 


def get_all_word_pairs_with_id(user_id):
    """Возвращает все пары (id, word_en, word_ru) для пользователя: сначала индивидуальные, затем общие, без дубликатов по word_en."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Индивидуальные слова пользователя
            cur.execute('SELECT id, word_en, word_ru FROM user_words WHERE user_id = %s', (user_id,))
            user_words = cur.fetchall()
            user_en = {w['word_en'] for w in user_words}
            # Общие слова, которых нет у пользователя
            cur.execute('SELECT id, word_en, word_ru FROM words')
            common_words = [w for w in cur.fetchall() if w['word_en'] not in user_en]
            # Объединяем
            all_words = user_words + common_words
            return [(w['id'], w['word_en'], w['word_ru']) for w in all_words] 


def get_random_words_for_user(user_id):
    """
    Возвращает 4 случайные пары (word_en, word_ru) из доступных пользователю слов.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''
                SELECT word_en, word_ru FROM (
                    SELECT word_en, word_ru FROM user_words WHERE user_id = %(user_id)s
                    UNION
                    SELECT word_en, word_ru FROM words
                ) as t
                ORDER BY RANDOM()
                LIMIT 4;
            ''', {'user_id': user_id})
            words = cur.fetchall()
            return [(w['word_en'], w['word_ru']) for w in words]

def log_correct_answer(user_id):
    """Засчитывает один правильный ответ за сегодняшний день."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            today = date.today()
            cur.execute('''
                INSERT INTO daily_user_progress (user_id, progress_date, correct_answers)
                VALUES (%s, %s, 1)
                ON CONFLICT (user_id, progress_date) DO UPDATE SET
                    correct_answers = daily_user_progress.correct_answers + 1;
            ''', (user_id, today))
            conn.commit()

def get_today_correct_answers(user_id):
    """Возвращает количество правильных ответов за сегодня."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            today = date.today()
            cur.execute(
                'SELECT correct_answers FROM daily_user_progress WHERE user_id = %s AND progress_date = %s',
                (user_id, today)
            )
            row = cur.fetchone()
            return row[0] if row else 0

def get_distractors(user_id, word_to_exclude_en):
    """Возвращает 3 случайные пары-неправильные ответы, исключая конкретное слово."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''
                SELECT t.word_en, t.word_ru FROM (
                    SELECT word_en, word_ru FROM user_words WHERE user_id = %(user_id)s
                    UNION
                    SELECT word_en, word_ru FROM words
                ) as t
                WHERE t.word_en != %(exclude)s
                ORDER BY RANDOM()
                LIMIT 3;
            ''', {'user_id': user_id, 'exclude': word_to_exclude_en})
            words = cur.fetchall()
            return [(w['word_en'], w['word_ru']) for w in words] 