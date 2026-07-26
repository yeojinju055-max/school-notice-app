import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")

# Supabase(Postgres) 접속 주소. Render/로컬 모두 환경변수 DATABASE_URL로 넣어줌.
DATABASE_URL = os.environ.get("DATABASE_URL")


# ---------- 데이터베이스 관련 함수 ----------

class DBConn:
    """
    기존 sqlite3 코드(conn.execute(...).fetchone() 같은 형태)를
    최대한 그대로 쓸 수 있도록 psycopg2를 감싸주는 작은 도우미 클래스.
    RealDictCursor를 써서 결과를 딕셔너리처럼 다룰 수 있게 함
    (notice['title'] 같은 식으로 그대로 사용 가능).
    """
    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self._conn.cursor()
        cur.executemany(sql, seq_of_params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db():
    pg_conn = psycopg2.connect(DATABASE_URL)
    return DBConn(pg_conn)


def init_db():
    """
    앱이 시작될 때(로컬 실행이든 gunicorn 배포든 항상) 테이블이 없으면 만들어줌.
    Postgres는 "CREATE TABLE IF NOT EXISTS"를 그대로 지원해서
    sqlite3 버전보다 오히려 마이그레이션 코드가 단순해짐.
    """
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student',
            subject TEXT,
            bio TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notices (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT NOT NULL,
            due_date TEXT,
            author_id INTEGER,
            author_name TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id SERIAL PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            student_id INTEGER,
            student_name TEXT,
            content TEXT NOT NULL,
            answer TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    conn.commit()

    # 확인용 샘플 공지가 하나도 없으면 몇 개 넣어둠
    count = conn.execute("SELECT COUNT(*) AS cnt FROM notices").fetchone()["cnt"]
    if count == 0:
        sample = [
            ("2학기 수행평가 일정 안내", "수학 수행평가는 5월 20일까지 제출입니다.", "학사", "관리자"),
            ("동아리 발표회 안내", "5월 25일 강당에서 동아리 발표회가 진행됩니다.", "행사", "관리자"),
            ("급식 메뉴 변경 안내", "이번주 목요일 급식 메뉴가 변경되었습니다.", "생활", "관리자"),
        ]
        conn.executemany(
            "INSERT INTO notices (title, content, category, author_name) VALUES (%s, %s, %s, %s)",
            sample
        )
        conn.commit()
    conn.close()


# 이 파일이 python app.py로 실행되든, gunicorn으로 실행되든
# 항상 데이터베이스 초기화가 되도록 여기서 바로 호출
init_db()


# ---------- 로그인 여부를 확인하는 데코레이터 ----------

def login_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("로그인이 필요합니다.")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


# ---------- 라우트(페이지) 정의 ----------

@app.route("/")
@login_required
def index():
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    conn = get_db()
    sql = "SELECT * FROM notices WHERE 1=1"
    params = []

    if query:
        sql += " AND (title ILIKE %s OR content ILIKE %s)"
        params.extend([f"%{query}%", f"%{query}%"])

    if category:
        sql += " AND category = %s"
        params.append(category)

    sql += " ORDER BY (due_date IS NULL), due_date ASC, created_at DESC"

    notices = conn.execute(sql, params).fetchall()
    categories = conn.execute("SELECT DISTINCT category FROM notices").fetchall()
    conn.close()

    return render_template(
        "index.html",
        notices=notices,
        categories=[c["category"] for c in categories],
        query=query,
        selected_category=category,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        is_teacher = request.form.get("is_teacher") == "on"
        subject = request.form.get("subject", "").strip()
        bio = request.form.get("bio", "").strip()

        if not username or not password:
            flash("아이디와 비밀번호를 모두 입력해주세요.")
            return redirect(url_for("register"))

        conn = get_db()
        existing = conn.execute(
            "SELECT id FROM users WHERE username = %s", (username,)
        ).fetchone()

        if existing:
            flash("이미 존재하는 아이디입니다.")
            conn.close()
            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)
        role = "teacher" if is_teacher else "student"
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, subject, bio)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (username, password_hash, role, subject or None, bio or None),
        )
        conn.commit()
        conn.close()

        flash("회원가입이 완료되었습니다. 로그인해주세요.")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = %s", (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash(f"{username}님 환영합니다!")
            return redirect(url_for("index"))
        else:
            flash("아이디 또는 비밀번호가 올바르지 않습니다.")
            return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("로그아웃 되었습니다.")
    return redirect(url_for("login"))


@app.route("/notice/<int:notice_id>")
@login_required
def notice_detail(notice_id):
    conn = get_db()
    notice = conn.execute(
        "SELECT * FROM notices WHERE id = %s", (notice_id,)
    ).fetchone()
    conn.close()

    if notice is None:
        flash("존재하지 않는 공지입니다.")
        return redirect(url_for("index"))

    is_author = notice["author_id"] == session.get("user_id")
    return render_template("detail.html", notice=notice, is_author=is_author)


@app.route("/notice/new", methods=["GET", "POST"])
@login_required
def notice_new():
    if request.method == "POST":
        title = request.form["title"].strip()
        content = request.form["content"].strip()
        category = request.form["category"].strip()
        due_date = request.form.get("due_date", "").strip()

        if not title or not content or not category:
            flash("제목, 내용, 분류를 모두 입력해주세요.")
            return redirect(url_for("notice_new"))

        conn = get_db()
        conn.execute(
            """
            INSERT INTO notices (title, content, category, due_date, author_id, author_name)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (title, content, category, due_date or None, session["user_id"], session["username"]),
        )
        conn.commit()
        conn.close()

        flash("공지가 등록되었습니다.")
        return redirect(url_for("index"))

    return render_template("notice_form.html", notice=None)


@app.route("/notice/<int:notice_id>/edit", methods=["GET", "POST"])
@login_required
def notice_edit(notice_id):
    conn = get_db()
    notice = conn.execute(
        "SELECT * FROM notices WHERE id = %s", (notice_id,)
    ).fetchone()

    if notice is None:
        conn.close()
        flash("존재하지 않는 공지입니다.")
        return redirect(url_for("index"))

    if notice["author_id"] != session.get("user_id"):
        conn.close()
        flash("작성자만 수정할 수 있습니다.")
        return redirect(url_for("notice_detail", notice_id=notice_id))

    if request.method == "POST":
        title = request.form["title"].strip()
        content = request.form["content"].strip()
        category = request.form["category"].strip()
        due_date = request.form.get("due_date", "").strip()

        if not title or not content or not category:
            conn.close()
            flash("제목, 내용, 분류를 모두 입력해주세요.")
            return redirect(url_for("notice_edit", notice_id=notice_id))

        conn.execute(
            "UPDATE notices SET title = %s, content = %s, category = %s, due_date = %s WHERE id = %s",
            (title, content, category, due_date or None, notice_id),
        )
        conn.commit()
        conn.close()

        flash("공지가 수정되었습니다.")
        return redirect(url_for("notice_detail", notice_id=notice_id))

    conn.close()
    return render_template("notice_form.html", notice=notice)


@app.route("/notice/<int:notice_id>/delete", methods=["POST"])
@login_required
def notice_delete(notice_id):
    conn = get_db()
    notice = conn.execute(
        "SELECT * FROM notices WHERE id = %s", (notice_id,)
    ).fetchone()

    if notice is None:
        conn.close()
        flash("존재하지 않는 공지입니다.")
        return redirect(url_for("index"))

    if notice["author_id"] != session.get("user_id"):
        conn.close()
        flash("작성자만 삭제할 수 있습니다.")
        return redirect(url_for("notice_detail", notice_id=notice_id))

    conn.execute("DELETE FROM notices WHERE id = %s", (notice_id,))
    conn.commit()
    conn.close()

    flash("공지가 삭제되었습니다.")
    return redirect(url_for("index"))


@app.route("/teachers")
@login_required
def teacher_list():
    conn = get_db()
    teachers = conn.execute(
        "SELECT * FROM users WHERE role = 'teacher' ORDER BY username"
    ).fetchall()
    conn.close()
    return render_template("teacher_list.html", teachers=teachers)


@app.route("/teacher/<int:teacher_id>", methods=["GET", "POST"])
@login_required
def teacher_profile(teacher_id):
    conn = get_db()
    teacher = conn.execute(
        "SELECT * FROM users WHERE id = %s AND role = 'teacher'", (teacher_id,)
    ).fetchone()

    if teacher is None:
        conn.close()
        flash("존재하지 않는 선생님 페이지입니다.")
        return redirect(url_for("teacher_list"))

    notices = conn.execute(
        "SELECT * FROM notices WHERE author_id = %s ORDER BY created_at DESC",
        (teacher_id,),
    ).fetchall()

    if request.method == "POST":
        content = request.form["content"].strip()
        if not content:
            flash("질문 내용을 입력해주세요.")
        else:
            conn.execute(
                """
                INSERT INTO questions (teacher_id, student_id, student_name, content)
                VALUES (%s, %s, %s, %s)
                """,
                (teacher_id, session["user_id"], session["username"], content),
            )
            conn.commit()
            flash("질문이 등록되었습니다.")
        conn.close()
        return redirect(url_for("teacher_profile", teacher_id=teacher_id))

    questions = conn.execute(
        "SELECT * FROM questions WHERE teacher_id = %s ORDER BY created_at DESC",
        (teacher_id,),
    ).fetchall()
    conn.close()

    is_owner = session.get("user_id") == teacher_id

    return render_template(
        "teacher_profile.html",
        teacher=teacher,
        notices=notices,
        questions=questions,
        is_owner=is_owner,
    )


@app.route("/teacher/<int:teacher_id>/question/<int:question_id>/answer", methods=["POST"])
@login_required
def question_answer(teacher_id, question_id):
    if session.get("user_id") != teacher_id:
        flash("담당 선생님만 답변할 수 있습니다.")
        return redirect(url_for("teacher_profile", teacher_id=teacher_id))

    answer = request.form["answer"].strip()
    conn = get_db()
    conn.execute(
        "UPDATE questions SET answer = %s WHERE id = %s AND teacher_id = %s",
        (answer, question_id, teacher_id),
    )
    conn.commit()
    conn.close()

    flash("답변이 등록되었습니다.")
    return redirect(url_for("teacher_profile", teacher_id=teacher_id))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
