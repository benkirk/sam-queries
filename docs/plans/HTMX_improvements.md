# HTMX Implementation Improvements

This document outlines identified deficiencies and recommended improvements for the HTMX-based form refactor in the SAM web application.

## 1. Error Handling (4xx/5xx Responses)

**Issue:** By default, HTMX does not perform swaps on `4xx` or `5xx` response codes. If a server-side error occurs (e.g., 500 Internal Server Error or 403 Forbidden), the UI will fail silently, leaving the user with no feedback.

**Recommendation:** Implement a global HTMX error handler in a static JavaScript file (e.g., `static/js/htmx-config.js`) to catch these events and display a toast or alert.

```javascript
document.body.addEventListener('htmx:responseError', function(evt) {
    const xhr = evt.detail.xhr;
    // Example: Show a Bootstrap toast or simple alert
    alert("Error " + xhr.status + ": " + (xhr.responseText || "Server Error"));
});
```

## 2. Session Expiry & Login Redirects

**Issue:** When a session expires, Flask-Login issues a 302 redirect to the login page. HTMX follows this redirect and swaps the full login page HTML into whatever small fragment container was targeted (e.g., an "Edit Allocation" modal body).

**Recommendation:** Update the unauthorized handler in the Flask application to detect HTMX requests and return a `HX-Redirect` header instead of a standard 302.

```python
@login_manager.unauthorized_handler
def unauthorized():
    if request.headers.get('HX-Request'):
        response = make_response("", 401)
        response.headers['HX-Redirect'] = url_for('auth.login')
        return response
    return redirect(url_for('auth.login'))
```

## 3. Declarative Modal Management

**Issue:** Current success fragments (e.g., `add_member_success_htmx.html`) use inline `<script>` tags with `setTimeout` to close Bootstrap modals. This is brittle and pollutes HTML fragments with logic.

**Recommendation:** Use the `HX-Trigger` header to signal modal closure and handle it via a global event listener.

**Flask Side:**
```python
response = make_response(render_template('...success.html'))
response.headers['HX-Trigger'] = '{"closeModal": "addMemberModal"}'
return response
```

**JavaScript Side (Global):**
```javascript
document.body.addEventListener('closeModal', function(evt) {
    const modalId = evt.detail.value;
    const modalEl = document.getElementById(modalId);
    if (modalEl) {
        const modal = bootstrap.Modal.getInstance(modalEl);
        if (modal) modal.hide();
    }
});
```

## 4. URL Routing in JavaScript

**Issue:** Some HTMX triggers or manual `htmx.ajax` calls use hardcoded URL strings (e.g., `'/admin/project/' + code`), which breaks if blueprint prefixes or route structures change.

**Recommendation:** Always use data attributes to pass server-generated URLs (via `url_for`) to the DOM, then read those attributes in JavaScript.

**HTML:**
```html
<div id="projectCardContainer" data-reload-url="{{ url_for('admin_dashboard.project_card', projcode='') }}"></div>
```

**JavaScript:**
```javascript
const baseUrl = container.dataset.reloadUrl;
htmx.ajax('GET', baseUrl + projcode, { target: '#projectCardContainer' });
```

## 5. Logic Separation & Namespace Pollution

**Issue:** Defining JavaScript functions inside HTMX fragments (e.g., `htmxSelectUser`) causes these functions to be re-evaluated every time the fragment is loaded and can lead to namespace collisions.

**Recommendation:** Move all helper functions to a dedicated static JavaScript file. Use HTMX's `hx-on` attributes or standard event delegation for interactivity within fragments.

## 6. CSRF Protection Integration

**Issue:** If CSRF protection is enabled globally via Flask-WTF, HTMX requests (POST/PUT/DELETE) will fail without a token. Fragments often omit the hidden CSRF input.

**Recommendation:** Configure HTMX globally to include the CSRF token in headers for all non-GET requests.

```javascript
document.body.addEventListener('htmx:configRequest', function(evt) {
    const token = document.querySelector('meta[name="csrf-token"]')?.content;
    if (token && evt.detail.verb !== 'get') {
        evt.detail.headers['X-CSRFToken'] = token;
    }
});
```

## 7. Accessibility (A11y)

**Issue:** 
- Search results use `<a>` with `href="#"`, which is incorrect for elements that perform JS actions.
- Loading indicators (spinners) are invisible to screen readers.

**Recommendation:**
- Use `<button type="button">` for search result items in `user_search_results_htmx.html`.
- Add `role="status"` and `aria-hidden="true"` appropriately to spinners.
- Use `aria-live="polite"` on containers where content is dynamically swapped.

```html
<button type="button" class="list-group-item list-group-item-action"
        onclick="htmxSelectUser(...)">
    ...
</button>

<span id="searchSpinner" class="htmx-indicator" role="status">
    <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
    <span class="visually-hidden">Loading...</span>
</span>
```
