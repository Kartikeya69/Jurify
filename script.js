/**
 * JuriFy - AI Legal Intelligence
 * Frontend JavaScript - Authentication, AI Processing, History, Analytics, Voice, PDF
 */

// ═══════════════════════════════════════════════════════════════
// CONFIGURATION & STATE
// ═══════════════════════════════════════════════════════════════

const API_URL = '';
let currentUser = null;
let currentLang = 'en';
let i18nData = {};
let currentResults = null;
let historyData = [];
let voiceUsed = false;
let sessionStartTime = Date.now();
let isFreeTier = false;
let freeQueriesRemaining = 5;

// ═══════════════════════════════════════════════════════════════
// INITIALIZATION
// ═══════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', async () => {
    await loadI18n();
    setupEventListeners();
    checkAuth();
    startTimeTracking();
});

async function loadI18n() {
    try {
        const response = await fetch('/i18n.json');
        i18nData = await response.json();
    } catch (error) {
        console.error('Failed to load i18n:', error);
    }
}

function setupEventListeners() {
    // Auth form
    document.getElementById('auth-form').addEventListener('submit', handleAuth);
    document.getElementById('switch-link').addEventListener('click', toggleAuthMode);
    
    // Language selector
    document.getElementById('lang-select').addEventListener('change', (e) => {
        setLanguage(e.target.value);
    });
}

// ═══════════════════════════════════════════════════════════════
// AUTHENTICATION
// ═══════════════════════════════════════════════════════════════

let isLoginMode = true;

function toggleAuthMode(e) {
    e.preventDefault();
    isLoginMode = !isLoginMode;
    
    const nameField = document.getElementById('name-field');
    const authTitle = document.getElementById('auth-title');
    const authBtn = document.getElementById('auth-btn');
    const switchText = document.getElementById('switch-text');
    const switchLink = document.getElementById('switch-link');
    
    if (isLoginMode) {
        nameField.classList.add('hidden');
        authTitle.innerHTML = '<i class="fas fa-sign-in-alt"></i> <span data-i18n="login">Login</span>';
        authBtn.innerHTML = '<span data-i18n="login_btn">Login</span> <i class="fas fa-arrow-right"></i>';
        switchText.setAttribute('data-i18n', 'no_account');
        switchLink.setAttribute('data-i18n', 'register_now');
    } else {
        nameField.classList.remove('hidden');
        authTitle.innerHTML = '<i class="fas fa-user-plus"></i> <span data-i18n="register">Register</span>';
        authBtn.innerHTML = '<span data-i18n="register_btn">Create Account</span> <i class="fas fa-arrow-right"></i>';
        switchText.setAttribute('data-i18n', 'have_account');
        switchLink.setAttribute('data-i18n', 'login_instead');
    }
    
    translateUI(currentLang);
}

async function handleAuth(e) {
    e.preventDefault();
    
    const email = document.getElementById('email-input').value.trim();
    const password = document.getElementById('password-input').value;
    const name = document.getElementById('name-input').value.trim();
    
    if (!email || !password || (!isLoginMode && !name)) {
        showToast(t('error_fill_fields'), 'error');
        return;
    }
    
    if (isLoginMode) {
        await loginUser(email, password);
    } else {
        await registerUser(name, email, password);
    }
}

async function registerUser(name, email, password) {
    try {
        const response = await fetch(`${API_URL}/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email, password })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showToast(t('success_register'));
            toggleAuthMode({ preventDefault: () => {} });
        } else {
            showToast(data.error || t('error_email_exists'), 'error');
        }
    } catch (error) {
        showToast('Registration failed', 'error');
    }
}

async function loginUser(email, password) {
    try {
        const response = await fetch(`${API_URL}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            localStorage.setItem('token', data.token);
            localStorage.setItem('user', JSON.stringify(data.user));
            currentUser = data.user;
            showApp();
        } else {
            showToast(t('error_invalid_creds'), 'error');
        }
    } catch (error) {
        showToast('Login failed', 'error');
    }
}

function logoutUser() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    currentUser = null;
    currentResults = null;
    isFreeTier = false;
    
    // Reset UI elements
    document.getElementById('free-tier-badge').classList.add('hidden');
    document.getElementById('history-btn').classList.remove('hidden');
    document.getElementById('analytics-btn').classList.remove('hidden');
    document.querySelector('.xp-strip').style.display = '';
    
    showAuth();
}

function getToken() {
    return localStorage.getItem('token');
}

function isAuthenticated() {
    return !!getToken();
}

function checkAuth() {
    const token = getToken();
    const user = localStorage.getItem('user');
    
    if (token && user) {
        currentUser = JSON.parse(user);
        showApp();
    } else {
        showAuth();
    }
}

function showAuth() {
    document.getElementById('auth-section').classList.remove('hidden');
    document.getElementById('app-section').classList.add('hidden');
}

function showApp() {
    document.getElementById('auth-section').classList.add('hidden');
    document.getElementById('app-section').classList.remove('hidden');
    document.getElementById('user-name').textContent = currentUser ? currentUser.name : 'Guest';
    
    if (!isFreeTier) {
        loadXP();
        fetchHistory();
        document.getElementById('free-tier-badge').classList.add('hidden');
    }
}

// ═══════════════════════════════════════════════════════════════
// FREE TIER MODE
// ═══════════════════════════════════════════════════════════════

function getClientId() {
    let clientId = localStorage.getItem('jurifyx_client_id');
    if (!clientId) {
        clientId = 'client_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
        localStorage.setItem('jurifyx_client_id', clientId);
    }
    return clientId;
}

async function tryFreeMode() {
    isFreeTier = true;
    currentUser = { name: 'Guest (Free Tier)', id: null };
    
    // Check free tier status
    await checkFreeStatus();
    
    // Show app in free mode
    document.getElementById('auth-section').classList.add('hidden');
    document.getElementById('app-section').classList.remove('hidden');
    document.getElementById('user-name').textContent = 'Guest';
    
    // Show free tier badge, hide XP strip for free users
    document.getElementById('free-tier-badge').classList.remove('hidden');
    document.getElementById('history-btn').classList.add('hidden');
    document.getElementById('analytics-btn').classList.add('hidden');
    document.querySelector('.xp-strip').style.display = 'none';
    
    updateFreeBadge();
}

async function checkFreeStatus() {
    try {
        const response = await fetch(`${API_URL}/free/status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ client_id: getClientId() })
        });
        
        if (response.ok) {
            const data = await response.json();
            freeQueriesRemaining = data.remaining;
            updateFreeBadge();
        }
    } catch (error) {
        console.error('Failed to check free status:', error);
    }
}

function updateFreeBadge() {
    const badge = document.getElementById('free-tier-badge');
    const countEl = document.getElementById('free-queries-left');
    
    if (countEl) countEl.textContent = freeQueriesRemaining;
    
    badge.classList.remove('warning', 'exhausted');
    if (freeQueriesRemaining <= 2 && freeQueriesRemaining > 0) {
        badge.classList.add('warning');
    } else if (freeQueriesRemaining <= 0) {
        badge.classList.add('exhausted');
    }
}

// ═══════════════════════════════════════════════════════════════
// AI PROCESSING
// ═══════════════════════════════════════════════════════════════

async function processIssue(skipCache = false) {
    const issue = document.getElementById('issue-input').value.trim();
    
    if (!issue) {
        showToast('Please describe your legal issue', 'error');
        return;
    }
    
    if (issue.length < 10) {
        showToast('Please provide more details about your issue', 'error');
        return;
    }
    
    // Check free tier limit
    if (isFreeTier && freeQueriesRemaining <= 0) {
        showToast('Daily limit reached! Login for unlimited access or wait 24 hours.', 'error');
        return;
    }
    
    const summarize = document.getElementById('summarize-toggle').checked;
    const analyzeBtn = document.getElementById('analyze-btn');
    const freshBtn = document.getElementById('fresh-btn');
    
    // Show loading state
    analyzeBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + t('analyzing');
    analyzeBtn.disabled = true;
    analyzeBtn.style.opacity = '0.7';
    if (freshBtn) {
        freshBtn.disabled = true;
        freshBtn.style.opacity = '0.7';
    }
    
    try {
        let response;
        
        if (isFreeTier) {
            // Use free tier endpoint
            response = await fetch(`${API_URL}/free/process`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    issue,
                    language: currentLang,
                    summarize,
                    client_id: getClientId()
                })
            });
        } else {
            // Use authenticated endpoint
            response = await fetch(`${API_URL}/process`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${getToken()}`
                },
                body: JSON.stringify({
                    issue,
                    language: currentLang,
                    summarize,
                    voice_used: voiceUsed,
                    skip_cache: skipCache
                })
            });
        }
        
        const data = await response.json();
        
        if (response.status === 429) {
            // Rate limit reached
            showToast(data.error || 'Daily limit reached!', 'error');
            freeQueriesRemaining = 0;
            updateFreeBadge();
            return;
        }
        
        if (response.ok && !data.error) {
            currentResults = data;
            displayResults(data);
            
            // Handle free tier response
            if (isFreeTier && data.queries_remaining !== undefined) {
                freeQueriesRemaining = data.queries_remaining;
                updateFreeBadge();
                showToast(`${t('analysis_complete')} (${freeQueriesRemaining} queries left today)`);
            } else {
                // Show cache status in toast for logged in users
                const cacheStatus = data.from_cache ? '⚡ (cached)' : '🌐 (fresh)';
                showToast(`${t('analysis_complete')} ${cacheStatus} +${data.xp_reward} ${t('xp_earned')}!`);
                loadXP();
                fetchHistory();
            }
            
            // Update cache indicator
            updateCacheIndicator(data.from_cache);
            
            trackAction('issue_processed');
            if (data.from_cache) trackAction('cache_hit');
            if (summarize) trackAction('summarizer_used');
            if (voiceUsed) trackAction('voice_used');
            trackLanguageUsed(currentLang);
            voiceUsed = false;
            
            // Clear input after successful analysis
            document.getElementById('issue-input').value = '';
        } else {
            // Show detailed error message
            const errorMsg = data.error || 'Processing failed. Please try again.';
            showToast(errorMsg, 'error');
            console.error('API Error:', errorMsg);
        }
    } catch (error) {
        console.error('Network Error:', error);
        showToast('Network error. Please check your connection and try again.', 'error');
    } finally {
        analyzeBtn.innerHTML = '<i class="fas fa-bolt"></i> <span data-i18n="analyze">' + t('analyze') + '</span>';
        analyzeBtn.disabled = false;
        analyzeBtn.style.opacity = '1';
        if (freshBtn) {
            freshBtn.disabled = false;
            freshBtn.style.opacity = '1';
        }
    }
}

function updateCacheIndicator(fromCache) {
    let indicator = document.getElementById('cache-indicator');
    if (!indicator) {
        indicator = document.createElement('div');
        indicator.id = 'cache-indicator';
        indicator.className = 'cache-indicator';
        const resultsSection = document.getElementById('results-cards');
        if (resultsSection) {
            resultsSection.insertBefore(indicator, resultsSection.firstChild);
        }
    }
    
    if (fromCache) {
        indicator.innerHTML = '<i class="fas fa-bolt"></i> Response from cache (API quota saved!)';
        indicator.className = 'cache-indicator cache-hit';
    } else {
        indicator.innerHTML = '<i class="fas fa-cloud"></i> Fresh response from Gemini API';
        indicator.className = 'cache-indicator cache-miss';
    }
    indicator.style.display = 'block';
}

async function clearCache() {
    if (!confirm('Clear all cached responses? This will force fresh API calls for all queries.')) return;
    
    try {
        const response = await fetch(`${API_URL}/cache/clear`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getToken()}` }
        });
        
        if (response.ok) {
            const data = await response.json();
            showToast(`Cache cleared! ${data.deleted} entries removed.`);
            loadCacheStats();
        } else {
            showToast('Failed to clear cache', 'error');
        }
    } catch (error) {
        showToast('Error clearing cache', 'error');
    }
}

async function loadCacheStats() {
    try {
        const response = await fetch(`${API_URL}/cache/stats`);
        if (response.ok) {
            const stats = await response.json();
            const statsEl = document.getElementById('cache-stats');
            if (statsEl) {
                statsEl.innerHTML = `
                    <span title="Cached queries">📦 ${stats.total_entries}</span>
                    <span title="Cache hits">⚡ ${stats.total_hits} hits</span>
                `;
            }
        }
    } catch (error) {
        console.error('Failed to load cache stats:', error);
    }
}

function displayResults(data) {
    document.getElementById('empty-state').classList.add('hidden');
    document.getElementById('results-cards').classList.remove('hidden');
    
    const sections = [
        { id: 'rights-content', key: 'rights' },
        { id: 'steps-content', key: 'steps' },
        { id: 'docs-content', key: 'docs' },
        { id: 'notice-content', key: 'notice' }
    ];
    
    sections.forEach(({ id, key }) => {
        const element = document.getElementById(id);
        const content = data[key] || '';
        typewriterEffect(content, element);
    });
}

function typewriterEffect(text, element, speed = 10) {
    element.textContent = '';
    let i = 0;
    
    function type() {
        if (i < text.length) {
            element.textContent += text.charAt(i);
            i++;
            setTimeout(type, speed);
        }
    }
    
    type();
}

// ═══════════════════════════════════════════════════════════════
// HISTORY
// ═══════════════════════════════════════════════════════════════

async function fetchHistory() {
    try {
        const response = await fetch(`${API_URL}/history`, {
            headers: { 'Authorization': `Bearer ${getToken()}` }
        });
        
        if (response.ok) {
            historyData = await response.json();
            renderHistory(historyData);
        }
    } catch (error) {
        console.error('Failed to fetch history:', error);
    }
}

function renderHistory(items) {
    const list = document.getElementById('history-list');
    
    if (!items.length) {
        list.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 40px;">${t('no_history')}</p>`;
        return;
    }
    
    list.innerHTML = items.map(item => `
        <div class="history-item" onclick="viewHistoryItem(${item.id})">
            <div class="history-item-header">
                <span class="history-item-lang">🌐 ${item.language.toUpperCase()}</span>
                <span class="history-item-xp">+${item.xp_reward} XP</span>
            </div>
            <div class="history-item-issue">${escapeHtml(item.issue)}</div>
            <div class="history-item-date">${formatDate(item.created_at)}</div>
            <div class="history-item-actions">
                <button class="btn-pill btn-cyan" onclick="event.stopPropagation(); viewHistoryItem(${item.id})">
                    <i class="fas fa-eye"></i> ${t('view')}
                </button>
                <button class="btn-pill btn-red" onclick="event.stopPropagation(); deleteHistoryItem(${item.id})">
                    <i class="fas fa-trash"></i> ${t('delete')}
                </button>
            </div>
        </div>
    `).join('');
}

async function viewHistoryItem(id) {
    try {
        const response = await fetch(`${API_URL}/history/${id}`, {
            headers: { 'Authorization': `Bearer ${getToken()}` }
        });
        
        if (response.ok) {
            const data = await response.json();
            currentResults = data;
            displayResults(data);
            closePanel();
        }
    } catch (error) {
        showToast('Failed to load history item', 'error');
    }
}

async function deleteHistoryItem(id) {
    if (!confirm('Delete this history item?')) return;
    
    try {
        const response = await fetch(`${API_URL}/history/${id}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${getToken()}` }
        });
        
        if (response.ok) {
            showToast('History item deleted');
            fetchHistory();
        }
    } catch (error) {
        showToast('Failed to delete', 'error');
    }
}

function searchHistory() {
    const term = document.getElementById('history-search').value.toLowerCase();
    const filtered = historyData.filter(item => 
        item.issue.toLowerCase().includes(term)
    );
    renderHistory(filtered);
}

async function clearAllHistory() {
    if (!confirm(t('confirm_clear'))) return;
    
    for (const item of historyData) {
        await fetch(`${API_URL}/history/${item.id}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${getToken()}` }
        });
    }
    
    showToast(t('history_cleared'));
    fetchHistory();
}

// ═══════════════════════════════════════════════════════════════
// XP & GAMIFICATION
// ═══════════════════════════════════════════════════════════════

async function loadXP() {
    try {
        const response = await fetch(`${API_URL}/xp`, {
            headers: { 'Authorization': `Bearer ${getToken()}` }
        });
        
        if (response.ok) {
            const data = await response.json();
            renderXP(data.total_xp, data.level, data.xp_in_level);
            renderBadges(data.badges);
        }
    } catch (error) {
        console.error('Failed to load XP:', error);
    }
}

function renderXP(totalXP, level, xpInLevel) {
    document.getElementById('user-level').textContent = level;
    document.getElementById('xp-text').textContent = `${totalXP} XP`;
    document.getElementById('xp-progress').style.width = `${xpInLevel}%`;
}

function renderBadges(badges) {
    const badgeElements = {
        bronze: document.getElementById('badge-bronze'),
        silver: document.getElementById('badge-silver'),
        gold: document.getElementById('badge-gold'),
        diamond: document.getElementById('badge-diamond')
    };
    
    Object.entries(badges).forEach(([key, earned]) => {
        if (badgeElements[key]) {
            badgeElements[key].classList.toggle('earned', earned);
        }
    });
}

// ═══════════════════════════════════════════════════════════════
// VOICE INPUT
// ═══════════════════════════════════════════════════════════════

let recognition = null;

function startVoiceInput() {
    if (!document.getElementById('voice-toggle').checked) {
        showToast('Please enable Voice Input toggle first', 'error');
        return;
    }
    
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
        showToast('Voice input not supported in this browser', 'error');
        return;
    }
    
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.lang = currentLang === 'en' ? 'en-US' : 
                       currentLang === 'hi' ? 'hi-IN' :
                       currentLang === 'mr' ? 'mr-IN' :
                       currentLang === 'ta' ? 'ta-IN' :
                       currentLang === 'bn' ? 'bn-IN' : 'en-US';
    recognition.continuous = false;
    recognition.interimResults = false;
    
    const recordBtn = document.getElementById('record-btn');
    recordBtn.innerHTML = '<i class="fas fa-microphone"></i> Listening...';
    recordBtn.style.borderColor = 'var(--cyan)';
    recordBtn.style.color = 'var(--cyan)';
    
    recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        document.getElementById('issue-input').value += transcript;
        voiceUsed = true;
        showToast('Voice input captured!');
    };
    
    recognition.onerror = (event) => {
        showToast('Voice recognition error: ' + event.error, 'error');
    };
    
    recognition.onend = () => {
        recordBtn.innerHTML = '<i class="fas fa-circle"></i> Record';
        recordBtn.style.borderColor = 'var(--red)';
        recordBtn.style.color = 'var(--red)';
    };
    
    recognition.start();
}

// ═══════════════════════════════════════════════════════════════
// PDF EXPORT
// ═══════════════════════════════════════════════════════════════

function exportPDF() {
    if (!currentResults || !currentResults.notice) {
        showToast('No notice to export', 'error');
        return;
    }
    
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    
    // Title
    doc.setFontSize(20);
    doc.setTextColor(147, 51, 234);
    doc.text('JuriFy Legal Notice', 105, 20, { align: 'center' });
    
    // Date
    doc.setFontSize(10);
    doc.setTextColor(100);
    doc.text(`Generated: ${new Date().toLocaleString()}`, 105, 30, { align: 'center' });
    
    // Notice content
    doc.setFontSize(12);
    doc.setTextColor(0);
    
    const lines = doc.splitTextToSize(currentResults.notice, 170);
    doc.text(lines, 20, 50);
    
    doc.save('jurifyx_notice.pdf');
    showToast('PDF exported successfully!');
}

// ═══════════════════════════════════════════════════════════════
// ANALYTICS
// ═══════════════════════════════════════════════════════════════

function getAnalytics() {
    return JSON.parse(localStorage.getItem('jurifyx_analytics') || JSON.stringify({
        issues_processed: 0,
        languages_used: {},
        voice_usage: 0,
        summarizer_usage: 0,
        cache_hits: 0,
        time_spent: 0
    }));
}

function saveAnalytics(analytics) {
    localStorage.setItem('jurifyx_analytics', JSON.stringify(analytics));
}

function trackAction(action) {
    const analytics = getAnalytics();
    
    switch (action) {
        case 'issue_processed':
            analytics.issues_processed++;
            break;
        case 'voice_used':
            analytics.voice_usage++;
            break;
        case 'summarizer_used':
            analytics.summarizer_usage++;
            break;
        case 'cache_hit':
            analytics.cache_hits++;
            break;
    }
    
    saveAnalytics(analytics);
}

function trackLanguageUsed(lang) {
    const analytics = getAnalytics();
    analytics.languages_used[lang] = (analytics.languages_used[lang] || 0) + 1;
    saveAnalytics(analytics);
}

function startTimeTracking() {
    setInterval(() => {
        if (isAuthenticated()) {
            const analytics = getAnalytics();
            analytics.time_spent += 10;
            saveAnalytics(analytics);
        }
    }, 10000);
}

function renderAnalytics() {
    const analytics = getAnalytics();
    const content = document.getElementById('analytics-content');
    
    const langList = Object.entries(analytics.languages_used)
        .map(([lang, count]) => `${lang.toUpperCase()}: ${count}`)
        .join(', ') || 'None';
    
    const timeMinutes = Math.floor(analytics.time_spent / 60);
    const cacheHits = analytics.cache_hits || 0;
    const cacheRate = analytics.issues_processed > 0 
        ? Math.round((cacheHits / analytics.issues_processed) * 100) 
        : 0;
    
    content.innerHTML = `
        <div class="analytics-item">
            <span class="analytics-label">${t('issues_processed')}</span>
            <span class="analytics-value">${analytics.issues_processed}</span>
        </div>
        <div class="analytics-item">
            <span class="analytics-label">⚡ ${t('cache_hits') || 'Cache Hits'}</span>
            <span class="analytics-value">${cacheHits} (${cacheRate}%)</span>
        </div>
        <div class="analytics-item">
            <span class="analytics-label">${t('languages_used')}</span>
            <span class="analytics-value">${langList}</span>
        </div>
        <div class="analytics-item">
            <span class="analytics-label">${t('voice_usage')}</span>
            <span class="analytics-value">${analytics.voice_usage}</span>
        </div>
        <div class="analytics-item">
            <span class="analytics-label">${t('summarizer_usage')}</span>
            <span class="analytics-value">${analytics.summarizer_usage}</span>
        </div>
        <div class="analytics-item">
            <span class="analytics-label">${t('time_spent')}</span>
            <span class="analytics-value">${timeMinutes} min</span>
        </div>
    `;
}

// ═══════════════════════════════════════════════════════════════
// INTERNATIONALIZATION
// ═══════════════════════════════════════════════════════════════

function setLanguage(lang) {
    currentLang = lang;
    localStorage.setItem('jurifyx_lang', lang);
    translateUI(lang);
}

function translateUI(lang) {
    const translations = i18nData[lang] || i18nData['en'];
    
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (translations[key]) {
            el.textContent = translations[key];
        }
    });
    
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        if (translations[key]) {
            el.placeholder = translations[key];
        }
    });
}

function t(key) {
    const translations = i18nData[currentLang] || i18nData['en'] || {};
    return translations[key] || key;
}

// ═══════════════════════════════════════════════════════════════
// UI HELPERS
// ═══════════════════════════════════════════════════════════════

function showPanel(panel) {
    closePanel();
    const panelEl = document.getElementById(`${panel}-panel`);
    panelEl.classList.remove('hidden');
    
    if (panel === 'analytics') {
        renderAnalytics();
    }
}

function closePanel() {
    document.querySelectorAll('.side-panel').forEach(p => p.classList.add('hidden'));
}

function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = type === 'success' ? `✓ ${message}` : `✗ ${message}`;
    toast.style.background = type === 'success' ? 'var(--emerald)' : 'var(--red)';
    toast.classList.remove('hidden');
    
    setTimeout(() => {
        toast.classList.add('hidden');
    }, 3000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatDate(dateStr) {
    return new Date(dateStr).toLocaleString();
}
