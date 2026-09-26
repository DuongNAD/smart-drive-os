# Contributing to SmartDrive-OS

Thank you for your interest in contributing to **SmartDrive-OS**! We welcome contributions from developers, AI engineers, and storage enthusiasts of all skill levels.

---

## 🌟 How Can You Contribute?

You can contribute in several ways:
1. **Report Bugs**: Found a bug or compatibility issue with an SSD format? Open an [Issue](https://github.com/DuongNAD/smart-drive-os/issues).
2. **Suggest Features**: Have ideas for new presets, CLI flags, or MCP tools? Let us know in an issue.
3. **Submit Code**: Implement new features, optimize algorithms, or improve cross-platform compatibility via Pull Requests.
4. **Improve Docs**: Help translate, clarify guides, or add troubleshooting tips.

---

## 🛠️ Development Workflow

### 1. Fork & Clone
Fork the repository on GitHub, then clone your fork locally:
```bash
git clone https://github.com/<your-username>/smart-drive-os.git
cd smart-drive-os
```

### 2. Environment Setup (Zero-Dependency)
SmartDrive-OS is built strictly using the **Python Standard Library**. No external virtual environment or `pip install` is required for core development.
```bash
# Verify Python version (>= 3.9 recommended)
python --version
```

### 3. Run Tests
Before making changes, verify that all existing tests pass:
```bash
python -m unittest discover tests
```

### 4. Create a Branch
```bash
git checkout -b feature/your-feature-name
```

### 5. Commit & Push
Follow standard Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`):
```bash
git commit -m "feat(search): add regex filter support"
git push origin feature/your-feature-name
```

### 6. Open a Pull Request
Go to the original repository [DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os) and open a Pull Request against the `main` branch.

---

## 📜 Code Guidelines
- **Zero External Dependencies**: Keep the core package 100% pure standard library.
- **Cross-Platform**: Ensure changes work identically on Windows, macOS, and Linux.
- **Safety First**: Never perform destructive unlinks without dry-run guards and whitelist protections.
- **Test Coverage**: Add test cases in `tests/` for any new logic or bug fixes.

---

## ⚖️ License
By contributing to SmartDrive-OS, you agree that your contributions will be licensed under its **MIT License**.
