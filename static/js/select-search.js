/* A type-to-filter box for a long <select>.
 *
 * Bharat Switchgear alone has a dozen item codes, and a plain dropdown means
 * scrolling to find one whose name you already know. This puts a search input
 * above the select and narrows its options as you type.
 *
 * The <select> stays the field that posts, so nothing about the form changes.
 *
 * Three rules it will not break:
 *   - the chosen option is never filtered away, so typing in the box can never
 *     quietly change the value that is about to be submitted;
 *   - options named in opts.keep (an action like "+ Add a new item code") are
 *     never filtered away either — they are needed most when nothing matched;
 *   - a select rebuilt by other code (a customer change repopulating its item
 *     codes) is picked up automatically and the box clears, so the filter never
 *     shows a stale list or a stale count.
 *
 * Usage:  attachSelectSearch(document.getElementById('roItemCode'),
 *                            {placeholder: 'Search code or description…',
 *                             hint: 'roCodeHint', keep: ['__new__']});
 */
(function (global) {
  'use strict';

  function attachSelectSearch(select, opts) {
    if (!select || select.dataset.searchAttached === '1') { return null; }
    opts = opts || {};
    var keep = opts.keep || [];

    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'select-search ' + (opts.className || '');
    input.placeholder = opts.placeholder || 'Search…';
    input.autocomplete = 'off';
    input.setAttribute('aria-label', opts.placeholder || 'Search the list below');
    select.parentNode.insertBefore(input, select);
    select.dataset.searchAttached = '1';

    var hint = opts.hint ? document.getElementById(opts.hint) : null;
    // Captured the moment we first overwrite it, not at attach time: these
    // hints are rewritten by the page as the form is filled in, so the text to
    // put back is whatever was there when the operator started typing.
    var savedHint = null;

    // The full list, kept apart from what is currently on screen.
    var master = [];
    // Set below. render() has to drain this observer's own queue, so the two
    // refer to each other.
    var observer = null;

    function snapshot() {
      master = Array.prototype.map.call(select.options, function (o) {
        return {value: o.value, text: o.textContent, disabled: o.disabled};
      });
    }

    function isKept(o) { return keep.indexOf(o.value) !== -1; }

    function render() {
      var term = (input.value || '').trim().toLowerCase();
      var chosen = select.value;
      var ph = master.length && !master[0].value ? master[0] : null;
      var real = master.filter(function (o) { return o.value && !isKept(o); });
      var kept = master.filter(isKept);

      var shown = real;
      if (term) {
        shown = real.filter(function (o) {
          return o.text.toLowerCase().indexOf(term) !== -1;
        });
        // Keep the current choice listed even when it does not match.
        if (chosen && !shown.some(function (o) { return o.value === chosen; })) {
          var sel = real.filter(function (o) { return o.value === chosen; })[0];
          if (sel) { shown = [sel].concat(shown); }
        }
      }

      select.innerHTML = '';
      if (ph) { select.add(new Option(ph.text, '')); }
      if (term && !shown.length) {
        var none = new Option('No match for “' + input.value.trim() + '”', '');
        none.disabled = true;
        select.add(none);
      }
      shown.concat(kept).forEach(function (o) {
        var el = new Option(o.text, o.value);
        el.disabled = o.disabled;
        select.add(el);
      });
      select.value = chosen;
      // Throw away the records we just generated. A boolean flag cannot do
      // this job: the observer callback is a microtask, so it runs after
      // render() has returned and would have already cleared the flag — it
      // would then read our own filtered list back as the new full list, and
      // emptying the box would never bring the other options back.
      if (observer) { observer.takeRecords(); }

      if (hint) {
        if (term) {
          if (savedHint === null) { savedHint = hint.textContent; }
          hint.textContent = 'Showing ' + shown.length + ' of ' + real.length + '.';
        } else if (savedHint !== null) {
          hint.textContent = savedHint;
          savedHint = null;
        }
      }
    }

    function syncEnabled() {
      // The search box is only useful while the select itself is usable.
      input.disabled = select.disabled;
      if (select.disabled) { input.value = ''; }
    }

    input.addEventListener('input', render);

    // Enter picks the only match rather than submitting the form.
    input.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') { return; }
      e.preventDefault();
      var real = Array.prototype.filter.call(select.options, function (o) {
        return o.value && !o.disabled && keep.indexOf(o.value) === -1;
      });
      if (real.length === 1) {
        select.value = real[0].value;
        select.dispatchEvent(new Event('change', {bubbles: true}));
      }
    });

    // Other code repopulating this select (picking a customer, say) replaces
    // its options wholesale. Re-snapshot, and drop the search term: the list is
    // a different one now, and the page has just set its own hint text.
    observer = new MutationObserver(function () {
      snapshot();
      syncEnabled();
      if (input.value) { input.value = ''; savedHint = null; }
    });
    observer.observe(select, {childList: true, attributes: true,
                              attributeFilter: ['disabled']});

    snapshot();
    syncEnabled();
    return {refresh: function () { snapshot(); syncEnabled(); render(); }};
  }

  global.attachSelectSearch = attachSelectSearch;
}(window));
