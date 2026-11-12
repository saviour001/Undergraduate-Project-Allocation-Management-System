# UPAMS — Undergraduate Project Allocation & Management System

A complete Flask-based web application for the **Federal Polytechnic, Nasarawa** — Department of Computer Science.

This is the HND final-year project: **Design and Implementation of an Undergraduate Project Allocation and Management System (A Case Study of The Federal Polytechnic, Nasarawa)**.

---

## ⚡ Quick Start (Windows)

1. **Install Python 3.7 or newer** from [python.org](https://www.python.org/downloads/). Tick **"Add Python to PATH"** during install.

2. **Open Command Prompt or PowerShell** in this folder:
   ```
   cd path\to\upams
   ```

3. **Install Flask** (only once):
   ```
   pip install flask werkzeug
   ```

4. **Run the app:**
   ```
   python app.py
   ```

5. **Open your browser:** `http://127.0.0.1:5000`

---

## 🔐 Default Login Credentials

| Role        | Username                          | Password      |
|-------------|-----------------------------------|---------------|
| Admin       | `admin`                           | `admin123`    |
| Supervisor  | `jmebawondu`                      | `super123`    |
| Supervisor  | `aibrahim`                        | `super123`    |
| Student     | `fpn_s05_2024_2025_cs_sw_001`     | `student123`  |

You can also log in with the raw registration number or staff ID:
- Student: `FPN/S05/2024/2025/CS/SW/001` / `student123`
- Supervisor: `FPN/STF/CS/001` / `super123`

---

## 🆕 NEW: Self-Registration (No CSV Upload Bottleneck)

Students and supervisors **register themselves** — no more admin typing 600 accounts.

**How it works:**
1. Admin uploads a CSV of student/supervisor identity records (or pre-loaded seed data is used).
2. The student/supervisor visits `/register`.
3. They enter their **registration number** or **staff ID**.
4. System looks it up → details auto-fill (read-only) → they set a password → **instant access**.

**No approval queue. No verification document upload.** Only registration numbers and staff IDs that are pre-loaded by admin can register, so no random sign-ups.

### Pre-loaded Test Records (Seeded)
- **30 students** across three reg formats:
  - `FPN/S05/2024/2025/CS/SW/001..010` — HND2 Software Engineering
  - `FPN/S05/2024/2025/CS/NC/001..010` — HND2 Networking & Cloud Computing
  - `FPN/S05/2024/2025/CS/001..010` — ND2 (not split by specialisation)
- **6 supervisors**: `FPN/STF/CS/001..006`
- 3 students and 2 supervisors are pre-claimed for testing; the rest are available for self-registration tests.

---

## 📥 CSV Import (Admin)

Navigate to **Identity Records** in the admin sidebar.

**Student CSV columns:**
```
registration_number, full_name, email, phone, gender, department, level, specialization
```
Only `ND2` and `HND2` rows are accepted; other levels are rejected.

**Supervisor CSV columns:**
```
staff_id, full_name, title, email, phone, gender, department
```

After upload you get a clear summary:
*"280 imported, 12 duplicates skipped, 3 bad rows skipped, 1 wrong level skipped."*

Sample CSV templates can be downloaded from the same page (Sample CSV button).

---

## 🚀 Allocation Modes — Fast for 600 Students

**Auto Even-Allocate.** One button at the top of the Allocations page. Divides all unallocated ND2/HND2 students as evenly as possible across all active supervisors. **No cap** — works for 4 supervisors and 600 students just as well.

**Bulk Allocate.** Tick checkboxes for any number of students, pick a supervisor from the dropdown, click *Allocate Selected*. Filter the unallocated table by level (HND2/ND2) and specialisation (Software Engineering / Networking & Cloud) for surgical batching.

**Reassign.** Each row in the Current Allocations table has its own dropdown — change a student's supervisor at any time. Useful when there's a misunderstanding between supervisor and supervisee.

**Already-Allocated Guard.** If you try to allocate a student who already has a supervisor, you'll see:
*"3 allocated. 2 skipped (already allocated): Friday Tanko → Dr Mebawondu Joseph O.; Samuel Ogiri → Engr Ibrahim Abdullahi"*
No silent double-bookings.

---

## 🔑 Password Reset (Admin)

On the Users page, every user has a **key icon button**. Click it to reset:
- **Default for students** → their registration number (e.g. `FPN/S05/2024/2025/CS/SW/001` becomes password `fpns0520242025cssw001`)
- **Default for supervisors** → their staff ID (e.g. `FPN/STF/CS/001` becomes `fpnstfcs001`)
- Or set a **custom temporary password** instead

After reset, the user is **forced to change** their password on next login. The Users page shows a "Reset pending" badge until they do.

This protects against malicious access — if anyone compromises a student's account, the admin can reset and the real student gets back in.

---

## 📞 Contact Visibility

**Student dashboard** shows a Supervisor card with full name, title, email, and phone — so students can reach out anytime.

**Supervisor dashboard** shows all supervisees in a table with their matric number, email, and phone — so supervisors can reach students directly.

---

## 📁 File Structure

```
upams/
├── app.py                     ← Flask backend (~2,300 lines)
├── INSTALL.md                 ← This file
├── static/
│   ├── fpn_logo.jpg           ← Real FPN crest (used everywhere)
│   ├── css/style.css          ← Full design system
│   ├── js/main.js
│   └── uploads/               ← Chapter files
├── templates/
│   ├── base.html, home.html, login.html, error.html
│   ├── register_choice.html, register_student.html, register_supervisor.html
│   ├── profile.html, announcements.html, notifications.html
│   ├── partials/logo.html
│   ├── admin/
│   │   ├── dashboard.html, users.html, user_form.html
│   │   ├── preload.html, preload_list.html     ← CSV import
│   │   ├── allocations.html (bulk select + filter)
│   │   ├── sessions.html, announcements.html
│   │   ├── reports.html, report_view.html, audit.html
│   ├── supervisor/ (9 templates)
│   └── student/    (4 templates)
└── instance/upams.db          ← SQLite (auto-created)
```

---

## 🚀 Features Summary

### Administrator
- Manual user creation (bypasses self-registration)
- CSV bulk import of identity records (students + supervisors)
- View pre-loaded records (claimed vs unclaimed) with search
- Allocate supervisors — auto, bulk-select, or single
- Filter unallocated by level (ND2/HND2) and specialisation
- Reassign students between supervisors
- Manage academic sessions
- Post departmental announcements
- Print-ready session reports with FPN letterhead header
- Export students/supervisors to CSV
- Full audit trail
- Reset any user's password (default or custom) with forced change

### Supervisor
- Self-register with staff ID (no admin approval)
- View all supervisees with full contact info
- Approve, reject, or propose project topics
- Review chapter uploads (versioned)
- Schedule and score defences (Presentation 30 + Project 40 + Chapters 30 = 100)
- Generate printable supervision reports

### Student
- Self-register with registration number (no admin approval)
- View supervisor's contact details on dashboard
- Submit project topic for approval
- Upload chapters (versioned — every resubmit tracked)
- Receive correction notes
- See defence schedule, final score, and grade

---

## 🛠 Resetting the Database

To start fresh with new seed data:

**Windows:** `del instance\upams.db` then `python app.py`
**Linux/Mac:** `rm instance/upams.db` then `python app.py`

---

## 🧠 Conventions

- Only **ND2 and HND2** students accepted (project levels only)
- HND2 specialisations: **Software Engineering**, **Networking & Cloud Computing**
- ND2: not split by specialisation
- Times New Roman 12pt for academic write-ups
- APA 7th edition with "According to [Author] (Year)" prefix
- Waterfall Model methodology
- Title format: *Design and Implementation of … (A Case Study of …)*

---

## 🆘 Troubleshooting

**"Port 5000 already in use"** — Change the port at the bottom of `app.py`:
```python
app.run(host='127.0.0.1', port=5050, debug=False)
```

**Icons not showing** — Need internet for Font Awesome CDN.

**Logo not showing** — Make sure `static/fpn_logo.jpg` exists in the project.

**Can't log in after reset** — Use the new password shown in the flash message.

---

© 2026 Federal Polytechnic, Nasarawa — Department of Computer Science
*Learning · Technology · Service*
