/* Turns a long <select> into a searchable dropdown.
 *
 * Bharat Switchgear alone has a dozen item codes, and a plain dropdown means
 * scrolling to find one whose name you already know. This replaces the select
 * with a single box you type into: the list opens underneath and narrows as
 * you type, the way the customer picker on the quotation form works.
 *
 * The <select> itself stays in the form and stays the field that posts, so
 * nothing server-side changes. It is only made invisible — not display:none,
 * because a `required` select that cannot be rendered makes the browser refuse
 * to submit with "an invalid form control is not focusable" and no message the
 * operator can see.
 *
 * Three rules it will not break:
 *   - the box always shows the option that is actually selected, so what you
 *     read is what is about to be submitted;
 *   - options named in opts.keep (an action like "+ Add a new item code") are
 *     never filtered away — they are wanted most when nothing matched;
 *   - a select rebuilt by other code (a customer change repopulating its item
 *     codes) is picked up automatically.
 *
 * Usage:  attachSelectSearch(document.getElementById('roItemCode'),
 *                            {placeholder: 'Search item code…',
 *                             hint: 'roCodeHint', keep: ['__new__']});
 */
(function (global) {
  'use strict';

  function attachSelectSearch(select, opts) {
    if (!select || select.dataset.searchAttached === '1') { return null; }
    opts = opts || {};
    var keep = opts.keep || [];

    // ---- structure -------------------------------------------------------
    var wrap = document.createElement('div');
    wrap.className = 'ss-wrap';
    select.parentNode.insertBefore(wrap, select);

    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'ss-input';
    // No name: this box is a control, not a field, and must never post.
    // Chrome ignores autocomplete="off" on anything its heuristics read as a
    // name or address field, and then drops its saved-addresses popup right
    // on top of our list. "new-password" is the value it does honour.
    input.autocomplete = 'new-password';
    input.setAttribute('autocorrect', 'off');
    input.setAttribute('autocapitalize', 'off');
    input.setAttribute('spellcheck', 'false');
    input.setAttribute('data-lpignore', 'true');     // LastPass
    input.setAttribute('data-form-type', 'other');   // Dashlane / 1Password
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-autocomplete', 'list');

    var caret = document.createElement('span');
    caret.className = 'ss-caret';
    caret.setAttribute('aria-hidden', 'true');

    var panel = document.createElement('div');
    panel.className = 'ss-panel';

    wrap.appendChild(input);
    wrap.appendChild(caret);
    wrap.appendChild(select);
    wrap.appendChild(panel);
    select.classList.add('ss-native');
    select.setAttribute('tabindex', '-1');
    select.dataset.searchAttached = '1';

    var hint = opts.hint ? document.getElementById(opts.hint) : null;
    // Captured when we first overwrite it, not at attach time: these hints are
    // rewritten by the page as the form is filled in, so what goes back is
    // whatever was there when the operator started typing.
    var savedHint = null;

    var master = [];       // the full list, kept apart from what is on screen
    var shown = [];        // what the panel is currently listing
    var hi = -1;           // highlighted row
    var open = false;

    function isKept(o) { return keep.indexOf(o.value) !== -1; }
    function placeholderOpt() {
      return master.length && !master[0].value ? master[0] : null;
    }
    function chosenOpt() {
      var v = select.value;
      if (!v) { return null; }
      return master.filter(function (o) { return o.value === v; })[0] || null;
    }

    function snapshot() {
      master = Array.prototype.map.call(select.options, function (o) {
        return {value: o.value, text: o.textContent.trim(), disabled: o.disabled};
      });
    }

    // ---- the closed box --------------------------------------------------
    function syncClosed() {
      var ph = placeholderOpt();
      input.disabled = select.disabled;
      var sel = chosenOpt();
      input.value = sel ? sel.text : '';
      // Disabled, the box carries the select's own message ("Select a customer
      // first…"); enabled, it invites you to type.
      input.placeholder = select.disabled && ph
        ? ph.text
        : (opts.placeholder || 'Search…');
      if (hint && savedHint !== null) {
        hint.textContent = savedHint;
        savedHint = null;
      }
    }

    // ---- the open list ---------------------------------------------------
    function matches(term) {
      var real = master.filter(function (o) { return o.value && !isKept(o); });
      var list = term
        ? real.filter(function (o) { return o.text.toLowerCase().indexOf(term) !== -1; })
        : real;
      return {list: list.concat(master.filter(isKept)), realCount: real.length,
              hitCount: list.length};
    }

    function render() {
      var term = open ? input.value.trim().toLowerCase() : '';
      // Typing nothing after opening means "show me everything", and so does a
      // term that still equals the current selection — otherwise opening the
      // box on a chosen value would list only that one.
      var sel = chosenOpt();
      if (sel && term === sel.text.toLowerCase()) { term = ''; }

      var m = matches(term);
      shown = m.list;
      panel.innerHTML = '';

      // Nothing matched. Say so even when a kept action row is still listed,
      // or the operator is left looking at "+ Add a new item code" with no
      // word about why their search found nothing.
      if (!m.hitCount) {
        var none = document.createElement('div');
        none.className = 'ss-none';
        none.textContent = term ? 'No match for “' + input.value.trim() + '”'
                                : 'Nothing to choose from';
        panel.appendChild(none);
      }
      shown.forEach(function (o, i) {
        var row = document.createElement('div');
        row.className = 'ss-opt' + (o.value === select.value ? ' sel' : '')
                      + (isKept(o) ? ' act' : '') + (i === hi ? ' hi' : '');
        row.textContent = o.text;
        row.dataset.value = o.value;
        if (o.disabled) { row.classList.add('off'); }
        panel.appendChild(row);
      });

      if (hint && term) {
        if (savedHint === null) { savedHint = hint.textContent; }
        hint.textContent = 'Showing ' + m.hitCount + ' of ' + m.realCount + '.';
      }
    }

    function openPanel() {
      if (select.disabled || open) { return; }
      open = true;
      wrap.classList.add('ss-open');
      input.setAttribute('aria-expanded', 'true');
      var sel = chosenOpt();
      hi = sel ? -1 : 0;
      render();
      // Opening on a chosen value selects its text, so typing replaces it.
      if (sel) { input.select(); }
    }

    function closePanel(restore) {
      if (!open) { return; }
      open = false;
      hi = -1;
      wrap.classList.remove('ss-open');
      input.setAttribute('aria-expanded', 'false');
      panel.innerHTML = '';
      // The box must never be left showing a half-typed term that is not the
      // selection; put the chosen option's text back.
      if (restore !== false) { syncClosed(); }
    }

    function choose(o) {
      if (!o || o.disabled) { return; }
      select.value = o.value;
      closePanel();
      select.dispatchEvent(new Event('change', {bubbles: true}));
      // The change handler may have rebuilt the select, so re-read it.
      syncClosed();
    }

    function move(step) {
      if (!open) { openPanel(); return; }
      if (!shown.length) { return; }
      hi = (hi + step + shown.length) % shown.length;
      render();
      var row = panel.children[hi];
      if (row && row.scrollIntoView) { row.scrollIntoView({block: 'nearest'}); }
    }

    // ---- events ----------------------------------------------------------
    input.addEventListener('focus', openPanel);
    input.addEventListener('mousedown', function () {
      // A click on an already-focused box should toggle, like a real select.
      if (open) { closePanel(); } else { setTimeout(openPanel, 0); }
    });
    input.addEventListener('input', function () {
      if (!open) { openPanel(); }
      hi = 0;
      render();
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Escape') { if (open) { e.stopPropagation(); closePanel(); } }
      else if (e.key === 'Enter') {
        if (!open) { return; }
        e.preventDefault();          // never submit the form from the list
        if (hi >= 0 && shown[hi]) { choose(shown[hi]); }
        else if (shown.length === 1) { choose(shown[0]); }
      } else if (e.key === 'Tab') { closePanel(); }
    });

    panel.addEventListener('mousedown', function (e) {
      e.preventDefault();            // keep focus; blur must not beat the click
      var row = e.target.closest('.ss-opt');
      if (!row || row.classList.contains('off')) { return; }
      choose(shown.filter(function (o) { return o.value === row.dataset.value; })[0]);
    });

    document.addEventListener('mousedown', function (e) {
      if (open && !wrap.contains(e.target)) { closePanel(); }
    });

    // Other code repopulating this select (picking a customer, say) replaces
    // its options wholesale. Re-read it and show whatever it now holds.
    var observer = new MutationObserver(function () {
      snapshot();
      closePanel(false);
      syncClosed();
    });
    observer.observe(select, {childList: true, attributes: true,
                              attributeFilter: ['disabled']});

    snapshot();
    syncClosed();
    return {refresh: function () { snapshot(); syncClosed(); }};
  }

  global.attachSelectSearch = attachSelectSearch;
}(window));
