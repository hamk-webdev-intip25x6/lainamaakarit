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

    function initStarPickers() {
        var pickers = document.querySelectorAll('[data-star-picker]');
        pickers.forEach(function (picker) {
            var labels = Array.prototype.slice.call(picker.querySelectorAll('.star-choice'));
            var statusNode = picker.querySelector('[data-star-status]');

            function getSelectedValue() {
                var checked = picker.querySelector('input[type="radio"]:checked');
                return checked ? parseInt(checked.value, 10) : 0;
            }

            function renderStars(activeUpTo, selectedVal) {
                labels.forEach(function (label) {
                    var input = label.querySelector('input[type="radio"]');
                    var glyph = label.querySelector('.star-visual');
                    var val = input ? parseInt(input.value, 10) : 0;
                    var isLit = val <= activeUpTo;

                    label.classList.toggle('is-lit', isLit);
                    label.classList.toggle('is-selected', val === selectedVal && selectedVal > 0);
                    if (glyph) {
                        glyph.textContent = isLit ? '\u2605' : '\u2606';
                    }
                });

                if (statusNode) {
                    statusNode.textContent = selectedVal > 0
                        ? 'Valittu: ' + selectedVal + ' / 5 tähteä'
                        : 'Ei vielä valittu';
                }
            }

            labels.forEach(function (label) {
                var input = label.querySelector('input[type="radio"]');
                var val = input ? parseInt(input.value, 10) : 0;

                if (input) {
                    input.addEventListener('change', function () {
                        var current = getSelectedValue();
                        renderStars(current, current);
                    });
                }

                label.addEventListener('mouseenter', function () {
                    renderStars(val, getSelectedValue());
                });
            });

            picker.addEventListener('mouseleave', function () {
                var current = getSelectedValue();
                renderStars(current, current);
            });

            var initial = getSelectedValue();
            renderStars(initial, initial);
        });
    }

    function initCharCounters() {
        var counters = document.querySelectorAll('[data-char-counter]');
        counters.forEach(function (counter) {
            var targetId = counter.getAttribute('data-char-counter');
            var maxLen = counter.getAttribute('data-max') || '2000';
            var field = targetId ? document.getElementById(targetId) : null;
            if (!field) {
                return;
            }
            function updateCount() {
                counter.textContent = field.value.length + ' / ' + maxLen;
            }
            field.addEventListener('input', updateCount);
            updateCount();
        });
    }

    window.lainamaakaritTheme = {
        applyTheme: applyTheme,
        getActiveTheme: getActiveTheme,
        VALID_THEMES: VALID_THEMES
    };

    function initAll() {
        initThemeSwitcher();
        initMobileNav();
        initLoanCountdowns();
        initStarPickers();
        initCharCounters();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }
})();
