// App State
let state = {
    chapters: [],
    currentChapterIndex: 0,
    settings: {
        fontSize: 18,
        lineHeight: 1.6,
        paraSpacing: 1.5,
        fontFamily: 'Inter',
        scrollSpeed: 5,
        isAutoScrolling: false,
        adFree: false
    },
    uiVisible: false
};

// DOM Elements
const elements = {
    content: document.getElementById('content'),
    header: document.getElementById('main-header'),
    bottomNav: document.getElementById('bottom-nav'),
    chapterTitle: document.getElementById('chapter-title'),
    modalChapterTitle: document.getElementById('modal-chapter-title'),
    chapterSelect: document.getElementById('chapter-select'),
    btnSettings: document.getElementById('btn-settings'),
    modalSettings: document.getElementById('settings-modal'),
    btnCloseSettings: document.getElementById('close-settings'),
    tapOverlay: document.getElementById('tap-overlay'),

    // Settings inputs
    fontSizeSlider: document.getElementById('font-size-slider'),
    lineHeightSlider: document.getElementById('line-height-slider'),
    lineHeightVal: document.getElementById('line-height-val'),
    paraSpacingSlider: document.getElementById('para-spacing-slider'),
    paraSpacingVal: document.getElementById('para-spacing-val'),
    scrollSpeedSlider: document.getElementById('scroll-speed-slider'),
    scrollSpeedVal: document.getElementById('scroll-speed-val'),
    btnAutoScroll: document.getElementById('toggle-autoscroll'),
    fontBtns: document.querySelectorAll('.font-btn'),
    adFreeToggle: document.getElementById('ad-free-toggle'),

    // Nav buttons
    prevBtn: document.getElementById('prev-chapter'),
    nextBtn: document.getElementById('next-chapter'),
    prevBtnBottom: document.getElementById('prev-chapter-bottom'),
    nextBtnBottom: document.getElementById('next-chapter-bottom')
};

// Initialize
async function init() {
    loadSettings();
    applySettings();
    setupEventListeners();
    await loadChapters();

    const lastChapter = localStorage.getItem('lastChapterIndex');
    if (lastChapter !== null) {
        loadChapter(parseInt(lastChapter));
    } else {
        loadChapter(0);
    }
}

// Data Loading
async function loadChapters() {
    try {
        const response = await fetch('./chapters.json');
        state.chapters = await response.json();

        elements.chapterSelect.innerHTML = state.chapters.map((ch, index) =>
            `<option value="${index}">Capítulo ${ch.number}: ${ch.title}</option>`
        ).join('');
    } catch (err) {
        console.error('Error loading chapters:', err);
    }
}

async function loadChapter(index) {
    if (index < 0 || index >= state.chapters.length) return;

    state.currentChapterIndex = index;
    const chapter = state.chapters[index];
    elements.chapterTitle.textContent = `Capítulo ${chapter.number}: ${chapter.title}`;
    elements.modalChapterTitle.textContent = `Chapter ${chapter.number} - Capítulo ${chapter.number}: ${chapter.title}`;
    elements.chapterSelect.value = index;
    localStorage.setItem('lastChapterIndex', index);

    window.scrollTo(0, 0);

    try {
        const response = await fetch(`./chapters/${chapter.file}`);
        const text = await response.text();

        // Split by newlines, skip first line (it's the title again usually in these files)
        const lines = text.split('\n');
        const contentLines = lines.slice(1);

        const formattedText = contentLines
            .filter(para => para.trim() !== '')
            .map(para => `<p>${para.trim()}</p>`)
            .join('');

        elements.content.innerHTML = formattedText;
        document.title = `${chapter.title} - Novela Reader`;
        hideUI();
    } catch (err) {
        console.error('Error loading chapter:', err);
    }
}

// Settings Management
function loadSettings() {
    const saved = localStorage.getItem('readerSettingsRefined');
    if (saved) {
        state.settings = { ...state.settings, ...JSON.parse(saved) };
    }
}

function saveSettings() {
    localStorage.setItem('readerSettingsRefined', JSON.stringify(state.settings));
}

function applySettings() {
    const root = document.documentElement;
    root.style.setProperty('--font-size', `${state.settings.fontSize}px`);
    root.style.setProperty('--line-height', state.settings.lineHeight);
    root.style.setProperty('--para-spacing', `${state.settings.paraSpacing}em`);
    root.style.setProperty('--font-family', state.settings.fontFamily);

    elements.fontSizeSlider.value = state.settings.fontSize;
    elements.lineHeightSlider.value = state.settings.lineHeight;
    elements.lineHeightVal.textContent = state.settings.lineHeight;
    elements.paraSpacingSlider.value = state.settings.paraSpacing;
    elements.paraSpacingVal.textContent = `${state.settings.paraSpacing}em`;
    elements.scrollSpeedSlider.value = state.settings.scrollSpeed;
    elements.scrollSpeedVal.textContent = state.settings.scrollSpeed;
    elements.adFreeToggle.checked = state.settings.adFree;

    elements.fontBtns.forEach(btn => {
        btn.classList.toggle('active', btn.dataset.font === state.settings.fontFamily);
    });

    if (state.settings.isAutoScrolling) {
        startAutoScroll();
    } else {
        stopAutoScroll();
    }
}

// UI Controls
function toggleUI() {
    state.uiVisible = !state.uiVisible;
    elements.header.classList.toggle('hidden', !state.uiVisible);
    elements.bottomNav.classList.toggle('hidden', !state.uiVisible);
}

function hideUI() {
    state.uiVisible = false;
    elements.header.classList.add('hidden');
    elements.bottomNav.classList.add('hidden');
}

// Auto Scroll Logic
let scrollInterval;
function startAutoScroll() {
    stopAutoScroll();
    state.settings.isAutoScrolling = true;
    elements.btnAutoScroll.innerHTML = '<span class="play-icon">||</span> Stop Auto Scroll';

    const intervalMs = 100 - (state.settings.scrollSpeed * 9);
    scrollInterval = setInterval(() => {
        window.scrollBy(0, 1);
        if ((window.innerHeight + window.scrollY) >= document.body.offsetHeight) {
            stopAutoScroll();
        }
    }, intervalMs);
}

function stopAutoScroll() {
    state.settings.isAutoScrolling = false;
    elements.btnAutoScroll.innerHTML = '<span class="play-icon">▶</span> Start Auto Scroll';
    clearInterval(scrollInterval);
}

// Event Listeners
function setupEventListeners() {
    elements.tapOverlay.addEventListener('click', toggleUI);

    elements.btnSettings.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        elements.modalSettings.classList.remove('hidden');
    });

    elements.btnCloseSettings.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        elements.modalSettings.classList.add('hidden');
    });

    elements.modalSettings.addEventListener('click', (e) => {
        if (e.target === elements.modalSettings) {
            elements.modalSettings.classList.add('hidden');
        }
    });

    // Prevent clicks inside modal from toggling the UI overlay
    const modalContent = elements.modalSettings.querySelector('.modal-content');
    if (modalContent) {
        modalContent.addEventListener('click', (e) => {
            e.stopPropagation();
        });
    }

    elements.fontSizeSlider.addEventListener('input', (e) => {
        state.settings.fontSize = parseInt(e.target.value);
        applySettings();
        saveSettings();
    });

    elements.lineHeightSlider.addEventListener('input', (e) => {
        state.settings.lineHeight = parseFloat(e.target.value);
        applySettings();
        saveSettings();
    });

    elements.paraSpacingSlider.addEventListener('input', (e) => {
        state.settings.paraSpacing = parseFloat(e.target.value);
        applySettings();
        saveSettings();
    });

    elements.fontBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            state.settings.fontFamily = btn.dataset.font;
            applySettings();
            saveSettings();
        });
    });

    elements.scrollSpeedSlider.addEventListener('input', (e) => {
        state.settings.scrollSpeed = parseInt(e.target.value);
        if (state.settings.isAutoScrolling) startAutoScroll();
        applySettings();
        saveSettings();
    });

    elements.btnAutoScroll.addEventListener('click', () => {
        if (state.settings.isAutoScrolling) {
            stopAutoScroll();
        } else {
            startAutoScroll();
            elements.modalSettings.classList.add('hidden');
            hideUI();
        }
        saveSettings();
    });

    elements.adFreeToggle.addEventListener('change', (e) => {
        state.settings.adFree = e.target.checked;
        document.querySelector('.toggle-status').textContent = state.settings.adFree ? 'On' : 'Off';
        saveSettings();
    });

    const goPrev = (e) => { e.stopPropagation(); loadChapter(state.currentChapterIndex - 1); };
    const goNext = (e) => { e.stopPropagation(); loadChapter(state.currentChapterIndex + 1); };

    elements.prevBtn.addEventListener('click', goPrev);
    elements.nextBtn.addEventListener('click', goNext);
    elements.prevBtnBottom.addEventListener('click', goPrev);
    elements.nextBtnBottom.addEventListener('click', goNext);

    elements.chapterSelect.addEventListener('click', (e) => e.stopPropagation());
    elements.chapterSelect.addEventListener('change', (e) => {
        loadChapter(parseInt(e.target.value));
    });
}

init();
