# OpenClaw Custom Skills

A collection of custom [OpenClaw](https://github.com/openclaw/openclaw) agent skills.

## Skills

| Skill | Description |
|-------|-------------|
| [att-invoices](./att-invoices/) | Download AT&T invoice PDFs via Chrome CDP + Playwright automation |
| [certify-expenses](./certify-expenses/) | Automate Certify/Emburse expense reports — create, upload receipts, attach, submit |

## Installation

Copy a skill folder into your OpenClaw skills directory:

```bash
cp -r att-invoices/ ~/.openclaw/skills/
```

Or symlink:

```bash
ln -s $(pwd)/att-invoices ~/.openclaw/skills/att-invoices
```

## Prerequisites

Each skill has its own prerequisites listed in its `SKILL.md`. Common requirements:

- [OpenClaw](https://github.com/openclaw/openclaw) installed and running
- Python 3.10+ (for Python-based skills)

## License

MIT
