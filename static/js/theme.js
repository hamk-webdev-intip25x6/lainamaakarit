(function () {
    'use strict';

    var VALID_THEMES = ['light', 'dark', 'high-light', 'high-dark'];
    var STORAGE_KEY = 'theme';

    function getStoredTheme() {
        try {
            return window.localStorage.getItem(STORAGE_KEY);
        } catch (e) {
            return null;
        }
    }

    function saveTheme(theme) {
        try {
            window.localStorage.setItem(STORAGE_KEY, theme);
        } catch (e) {}
    }

    function getActiveTheme() {
        var current = document.documentElement.getAttribute('data-theme');
        if (VALID_THEMES.indexOf(current) !== -1) {
            return current;
        }
        var stored = getStoredTheme();
        if (VALID_THEMES.indexOf(stored) !== -1) {
            return stored;
        }
        return 'light';
    }

    function applyTheme(theme, persist) {
        if (VALID_THEMES.indexOf(theme) === -1) {
            theme = 'light';
        }
        document.documentElement.setAttribute('data-theme', theme);
        if (persist) {
            saveTheme(theme);
        }
        var select = document.getElementById('theme-select');
        if (select && select.value !== theme) {
            select.value = theme;
        }
    }

    function initThemeSwitcher() {
        var activeTheme = getActiveTheme();
        applyTheme(activeTheme, false);

        var select = document.getElementById('theme-select');
        if (select) {
            select.addEventListener('change', function (event) {
                applyTheme(event.target.value, true);
            });
        }
    }

    function initMobileNav() {
        var toggle = document.getElementById('nav-toggle');
        var menu = document.getElementById('nav-menu');
        if (!toggle || !menu) {
            return;
        }
        toggle.addEventListener('click', function () {
            var isOpen = menu.classList.toggle('is-open');
            toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });
    }

    function formatRemaining(ms) {
        if (ms <= 0) {
            return '(laina-aika päättynyt)';
        }
        var totalSeconds = Math.ceil(ms / 1000);
        var days = Math.floor(totalSeconds / 86400);
        var hours = Math.floor((totalSeconds % 86400) / 3600);
        var minutes = Math.floor((totalSeconds % 3600) / 60);
        var seconds = totalSeconds % 60;

        if (days > 0) {
            return '(' + days + ' vrk ' + hours + ' h jäljellä)';
        }
        if (hours > 0) {
            return '(' + hours + ' h ' + minutes + ' min jäljellä)';
        }
        if (minutes > 0) {
            return '(' + minutes + ' min ' + seconds + ' s jäljellä)';
        }
        return '(' + seconds + ' s jäljellä)';
    }

    function initLoanCountdowns() {
        var nodes = document.querySelectorAll('.loan-countdown[data-expires-at]');
        if (!nodes.length) {
            return;
        }

        function updateAll() {
            var now = Date.now();
            nodes.forEach(function (node) {
                var target = Date.parse(node.getAttribute('data-expires-at'));
                if (!isNaN(target)) {
                    node.textContent = formatRemaining(target - now);
                }
            });
        }

        updateAll();
        window.setInterval(updateAll, 1000);
    }

    window.lainamaakaritTheme = {
        applyTheme: applyTheme,
        getActiveTheme: getActiveTheme,
        VALID_THEMES: VALID_THEMES
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initThemeSwitcher();
            initMobileNav();
            initLoanCountdowns();
        });
    } else {
        initThemeSwitcher();
        initMobileNav();
        initLoanCountdowns();
    }
})();
