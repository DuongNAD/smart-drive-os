# GitHub Remote Push Guide for SmartDrive-OS

This 1-page guide walks you through connecting your local **SmartDrive-OS** repository to your personal GitHub account and pushing your initial release (`v1.0.0`).

---

## Prerequisites

1. **Git** installed on your system (`git --version`).
2. A **GitHub account** (https://github.com).
3. Configured Git identity:
   ```bash
   git config --global user.name "Your Name"
   git config --global user.email "your.email@example.com"
   ```

---

## Step 1: Create a New GitHub Repository

1. Log into your GitHub account and navigate to: https://github.com/new
2. Enter **Repository name**: `smart-drive-os` (or your preferred name).
3. Choose **Public** or **Private**.
4. **IMPORTANT**: Leave all initialization checkboxes **UNCHECKED**:
   - ❌ Do NOT check *Add a README file*
   - ❌ Do NOT add *.gitignore*
   - ❌ Do NOT choose a *license*
   *(SmartDrive-OS already includes production-grade README, .gitignore, and MIT LICENSE).*
5. Click **Create repository**.

---

## Step 2: Add Remote and Push

Open a terminal (PowerShell, Command Prompt, or Terminal) in the `smart_drive_os` root folder:

### Option A: Using HTTPS (Recommended for Personal Access Tokens)

```bash
# 1. Add your remote origin (replace <username> with your GitHub handle)
git remote add origin https://github.com/<username>/smart-drive-os.git

# 2. Rename default branch to main (if not already main)
git branch -M main

# 3. Push commits and tags to GitHub
git push -u origin main
```

### Option B: Using SSH Keys

```bash
# 1. Add remote using SSH URL
git remote add origin git@github.com:<username>/smart-drive-os.git

# 2. Rename default branch to main
git branch -M main

# 3. Push commits and tags to GitHub
git push -u origin main
```

---

## Step 3: Push Release Tags (Optional)

If you have created a release tag (such as `v1.0.0`):

```bash
git tag -a v1.0.0 -m "Release v1.0.0: SmartDrive-OS Standalone Package"
git push origin v1.0.0
```

---

## Troubleshooting & Tips

- **Authentication Failed (HTTPS)**: GitHub requires Personal Access Tokens (PAT) instead of account passwords for HTTPS. Generate one under **GitHub Settings > Developer Settings > Personal Access Tokens (classic)** with `repo` scope.
- **Remote Already Exists**: If you need to change your remote URL:
  ```bash
  git remote set-url origin https://github.com/<username>/smart-drive-os.git
  ```
- **exFAT Safe Commits**: Avoid committing while background processes are writing directly to `.smart_drive/index.db`. The included `.gitignore` automatically keeps SQLite databases and OS junk out of version control.
