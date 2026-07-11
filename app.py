from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "change-this-secret-key"  # 실제 배포 시엔 반드시 바꾸세요

DB_PATH = os.path.join(os.path.dirname(__file__), "notice.db")


# ---------- 데이터베이스 관련 함수 ----------

def get_db():
    """
    필요할 때마다 새 연결을 만들어 쓰는 함수.
    row_factory를 sqlite3.Row로 지정하면 결과를 딕셔너리처럼 다룰 수 있어서
    템플릿(HTML)에서 notice['title'] 같은 식으로 바로 꺼내 쓸 수 있음.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    앱을 처음 실행할 때 테이블이 없으면 만들어주는 함수.
    users: 로그인용 계정 정보
    notices: 공지사항 (제목, 내용, 분류, 작성일)
    """
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student',
            subject TEXT,
            bio TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT NOT NULL,
            due_date TEXT,
            author_id INTEGER,
            author_name TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            student_id INTEGER,
            student_name TEXT,
            content TEXT NOT NULL,
            answer TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.commit()

    # 이미 만들어진 테이블에 새 컬럼이 없을 수도 있으니
    # (예전 버전으로 실행해본 적 있는 경우) 없으면 추가해줌
    notice_cols = [row["name"] for row in conn.execute("PRAGMA table_info(notices)")]
    if "author_id" not in notice_cols:
        conn.execute("ALTER TABLE notices ADD COLUMN author_id INTEGER")
    if "author_name" not in notice_cols:
        conn.execute("ALTER TABLE notices ADD COLUMN author_name TEXT")
    if "due_date" not in notice_cols:
        conn.execute("ALTER TABLE notices ADD COLUMN due_date TEXT")

    user_cols = [row["name"] for row in conn.execute("PRAGMA table_info(users)")]
    if "role" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'")
    if "subject" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN subject TEXT")
    if "bio" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN bio TEXT")
    conn.commit()

    # 테스트용 샘플 공지가 하나도 없으면 몇 개 넣어둠 (기능 확인용)
    count = conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
    if count == 0:
        sample = [
            ("2학기 수행평가 일정 안내", "수학 수행평가는 5월 20일까지 제출입니다.", "학사"),
            ("동아리 발표회 안내", "5월 25일 강당에서 동아리 발표회가 진행됩니다.", "행사"),
            ("급식 메뉴 변경 안내", "이번주 목요일 급식 메뉴가 변경되었습니다.", "생활"),
        ]
        conn.executemany(
            "INSERT INTO notices (title, content, category, author_name) VALUES (?, ?, ?, ?)",
            [(t, c, cat, "관리자") for t, c, cat in sample]
        )
        conn.commit()
    conn.close()


# ---------- 로그인 여부를 확인하는 데코레이터 ----------

def login_required(view_func):
    """
    이 데코레이터를 붙인 페이지는 로그인 안 하면 접근이 막힘.
    session에 'user_id'가 있는지로 로그인 여부를 판단.
    """
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
    """
    메인 페이지 = 공지 목록.
    검색어(q)나 분류(category)가 URL에 있으면 그 조건으로 필터링해서 보여줌.
    예: /?q=수행평가&category=학사
    """
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    conn = get_db()
    sql = "SELECT * FROM notices WHERE 1=1"
    params = []

    if query:
        # 제목이나 내용 중 하나라도 검색어를 포함하면 결과에 나오게 함
        sql += " AND (title LIKE ? OR content LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%"])

    if category:
        sql += " AND category = ?"
        params.append(category)

    sql += " ORDER BY (due_date IS NULL), due_date ASC, created_at DESC"

    notices = conn.execute(sql, params).fetchall()

    # 분류 버튼 목록을 만들기 위해 현재 존재하는 카테고리 종류를 가져옴
    categories = conn.execute(
        "SELECT DISTINCT category FROM notices"
    ).fetchall()
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
        # 체크박스는 체크 안 하면 아예 폼 데이터에 안 담겨오기 때문에
        # request.form.get()으로 안전하게 꺼내야 함 (없으면 None)
        is_teacher = request.form.get("is_teacher") == "on"
        subject = request.form.get("subject", "").strip()
        bio = request.form.get("bio", "").strip()

        if not username or not password:
            flash("아이디와 비밀번호를 모두 입력해주세요.")
            return redirect(url_for("register"))

        conn = get_db()
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()

        if existing:
            flash("이미 존재하는 아이디입니다.")
            conn.close()
            return redirect(url_for("register"))

        # 비밀번호를 그대로 저장하면 안 되니까 해시로 변환해서 저장
        password_hash = generate_password_hash(password)
        role = "teacher" if is_teacher else "student"
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, subject, bio)
            VALUES (?, ?, ?, ?, ?)
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
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

        # 사용자가 존재하고, 저장된 해시와 입력한 비밀번호가 일치하는지 확인
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
        "SELECT * FROM notices WHERE id = ?", (notice_id,)
    ).fetchone()
    conn.close()

    if notice is None:
        flash("존재하지 않는 공지입니다.")
        return redirect(url_for("index"))

    # 지금 로그인한 사람이 이 글의 작성자인지 확인 (수정/삭제 버튼 노출 여부에 사용)
    is_author = notice["author_id"] == session.get("user_id")

    return render_template("detail.html", notice=notice, is_author=is_author)


@app.route("/notice/new", methods=["GET", "POST"])
@login_required
def notice_new():
    """
    새 공지 등록 페이지.
    작성자 정보(author_id, author_name)를 세션에서 가져와 같이 저장한다.
    """
    if request.method == "POST":
        title = request.form["title"].strip()
        content = request.form["content"].strip()
        category = request.form["category"].strip()
        due_date = request.form.get("due_date", "").strip()  # 비워두면 마감일 없는 일반 공지

        if not title or not content or not category:
            flash("제목, 내용, 분류를 모두 입력해주세요.")
            return redirect(url_for("notice_new"))

        conn = get_db()
        conn.execute(
            """
            INSERT INTO notices (title, content, category, due_date, author_id, author_name)
            VALUES (?, ?, ?, ?, ?, ?)
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
        "SELECT * FROM notices WHERE id = ?", (notice_id,)
    ).fetchone()

    if notice is None:
        conn.close()
        flash("존재하지 않는 공지입니다.")
        return redirect(url_for("index"))

    # 작성자 본인이 아니면 수정 못 하게 막음
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
            "UPDATE notices SET title = ?, content = ?, category = ?, due_date = ? WHERE id = ?",
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
        "SELECT * FROM notices WHERE id = ?", (notice_id,)
    ).fetchone()

    if notice is None:
        conn.close()
        flash("존재하지 않는 공지입니다.")
        return redirect(url_for("index"))

    if notice["author_id"] != session.get("user_id"):
        conn.close()
        flash("작성자만 삭제할 수 있습니다.")
        return redirect(url_for("notice_detail", notice_id=notice_id))

    conn.execute("DELETE FROM notices WHERE id = ?", (notice_id,))
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
        "SELECT * FROM users WHERE id = ? AND role = 'teacher'", (teacher_id,)
    ).fetchone()

    if teacher is None:
        conn.close()
        flash("존재하지 않는 선생님 페이지입니다.")
        return redirect(url_for("teacher_list"))

    # 이 선생님이 올린 공지 목록
    notices = conn.execute(
        "SELECT * FROM notices WHERE author_id = ? ORDER BY created_at DESC",
        (teacher_id,),
    ).fetchall()

    # 질문 게시판에 새 질문 등록 (학생이 폼 제출했을 때)
    if request.method == "POST":
        content = request.form["content"].strip()
        if not content:
            flash("질문 내용을 입력해주세요.")
        else:
            conn.execute(
                """
                INSERT INTO questions (teacher_id, student_id, student_name, content)
                VALUES (?, ?, ?, ?)
                """,
                (teacher_id, session["user_id"], session["username"], content),
            )
            conn.commit()
            flash("질문이 등록되었습니다.")
        conn.close()
        return redirect(url_for("teacher_profile", teacher_id=teacher_id))

    questions = conn.execute(
        "SELECT * FROM questions WHERE teacher_id = ? ORDER BY created_at DESC",
        (teacher_id,),
    ).fetchall()
    conn.close()

    # 지금 로그인한 사람이 이 페이지의 주인(선생님 본인)인지 확인
    # → 본인이면 질문에 답변할 수 있는 입력창을 보여줌
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
    # 이 페이지의 주인(선생님 본인)만 답변을 남길 수 있게 막음
    if session.get("user_id") != teacher_id:
        flash("담당 선생님만 답변할 수 있습니다.")
        return redirect(url_for("teacher_profile", teacher_id=teacher_id))

    answer = request.form["answer"].strip()
    conn = get_db()
    conn.execute(
        "UPDATE questions SET answer = ? WHERE id = ? AND teacher_id = ?",
        (answer, question_id, teacher_id),
    )
    conn.commit()
    conn.close()

    flash("답변이 등록되었습니다.")
    return redirect(url_for("teacher_profile", teacher_id=teacher_id))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0",port=5000,debug=True)