<div align="center">
  <img src="assets/pc_booster_icon.png" width="96" alt="PC Booster Pro logo">
  <h1>⚡ PC Booster Pro 2026</h1>
  <p><strong>Autonomous Zero-Kill Windows Optimization Suite & Real-Time Sentinel</strong></p>
  <p>
    <a href="https://github.com/Mayer-ELbot/PC-Booster/releases/latest"><img alt="Download for Windows" src="https://img.shields.io/badge/Download_for_Windows-v1.0.0_Official-00f5a0?style=for-the-badge&logo=windows&logoColor=080d10"></a>
    <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-00d9f5?style=for-the-badge"></a>
    <img alt="Python 3.14" src="https://img.shields.io/badge/Python-3.14_Native-859f97?style=for-the-badge&logo=python&logoColor=white">
    <img alt="Zero-Kill Safe" src="https://img.shields.io/badge/Safety-Zero--Kill_Non--Destructive-ffb800?style=for-the-badge">
  </p>
</div>

---

## ⚡ What is PC Booster Pro? / ما هو البرنامج؟

**PC Booster Pro** is an autonomous, high-performance Windows optimization suite designed for gamers, creators, and power users who want peak responsiveness **without destructive force-killing or mystery registry bloat**.

Unlike aggressive task-killers, PC Booster Pro adheres strictly to the **Zero-Kill Safety Manifesto**:
- **Never terminates active user applications or games**.
- Proactively compacts idle working-set memory (`EmptyWorkingSet`), suppresses background CPU spikes (EcoQoS), and boosts active foreground game priority.
- 100% Native, zero telemetry, local-first, and fully reversible.

---

## 🚀 Key Features / أهم المميزات

### 🤖 1. Autonomous Auto-Pilot Sentinel (المحرك الذكي الصامت)
- **Foreground Turbo Priority Shield**: Automatically detects the active game or software (`GetForegroundWindow`) and elevates its thread scheduling priority (`Above Normal / High`) for maximum FPS and low input latency.
- **Background Bloatware & Memory Leak Tamer**: Silently flushes unused physical memory pages from idle background processes back to Windows without closing them.
- **Voice & Stream Shield**: Dedicated immunity protection for `Discord`, `Spotify`, `OBS`, `Steam`, ensuring zero voice stutter or audio lag.
- **Emergency Auto-Recovery**: If system memory usage exceeds 88%, the sentinel automatically triggers a non-destructive recovery sweep.

### ⚡ 2. 1-Click Quick Boost (تسريع وتنظيف فوري بضغطة زر)
- Instantly flushes idle working sets across background processes, flushes the Windows DNS resolver cache (`DnsFlushResolverCache`), and restores instant OS responsiveness.
- Smooth number counter HUD displaying the exact megabytes of RAM freed in real-time.

### 🧹 3. Multi-Target Deep Storage Cleaner (المنظف الشامل)
- Scans and cleans 6 distinct targets:
  1. User Temporary Files (`%TEMP%`)
  2. Windows System Temp (`C:\Windows\Temp`)
  3. Google Chrome Web Cache
  4. Microsoft Edge Web Cache
  5. Windows Crash Dumps (`%LOCALAPPDATA%\CrashDumps`)
  6. Windows Recycle Bin (with 1-click safe empty)

### 🛠️ 4. Reversible Windows & Gaming Tweaks (تعديلات ويندوز الآمنة)
- **Windows Game Mode**: Optimizes Windows CPU scheduling for games.
- **Instant Menu Response (10ms)**: Drops right-click and Start Menu delay from 400ms to 10ms.
- **Disable Background UWP Apps**: Stops Windows Store apps from running in the background.
- **Disable Bing Web Suggestions**: Accelerates Start Menu local searches.
- **Reduce Window Animations & Acrylic Blur**: GPU power savers.
- *Every tweak is backed up locally before change and restorable with 1 click.*

### 💻 5. Hardware Diagnostics & System Deck (مواصفات العتاد)
- Comprehensive live inventory of Processor model, physical cores, logical threads, clock speed, RAM usage split, GPU accelerators, multi-drive partition capacities, Windows build, and system uptime.

### 🎨 6. 2026 Cyber-Obsidian UI with Fluid Animations (واجهة نيون وعدادات دائرية)
- High-performance Canvas vector circular donut gauges for CPU, Memory, Disk, and GPU with smooth 50 FPS lerp interpolation.
- Pulsing neon sentinel radar beacon.
- Animated non-blocking floating Cyber Toasts.
- Complete Bilingual Support with one-click toggle (`🌐 عربي / English`).

### 🖥️ 7. Enhanced System Tray Experience (شريط المهام)
- Runs quietly in the tray with quick-access controls: Quick Boost Now, Open Dashboard, Settings, and Clean Exit.

---

## 📥 Download Standalone Executable / التحميل المباشر

Download the latest pre-compiled single-file executable:

👉 **[Download PC Booster Pro for Windows](https://github.com/Mayer-ELbot/PC-Booster/releases/latest/download/PC.Booster.exe)**

- **Single Portable File**: 100% self-contained, no Python or external runtimes required.
- **Platform**: Windows 10 / Windows 11 (64-bit).

---

## 🛠️ Build from Source / البناء من المصدر

### Requirements:
- Windows 10/11
- Python 3.14 (or 3.12+)
- Packages in `requirements.txt` (`psutil`, `pystray`, `Pillow`, `pyinstaller`)

```powershell
# 1. Install dependencies
py -3.14 -m pip install -r requirements.txt

# 2. Run from source
py -3.14 app.py

# 3. Build standalone single-file EXE
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The compiled binary will be placed at `dist/PC Booster.exe`.

---

## 🧪 Automated Testing / التحقق والاختبار

Run the complete test suite:

```powershell
py -3.14 -m unittest discover -s tests -p "test_*.py" -v
```

---

## 📄 License

PC Booster Pro is released under the [MIT License](LICENSE).
Built with craftsmanship for Windows users who demand peak performance without compromise.
