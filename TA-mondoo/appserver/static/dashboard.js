// dashboard.js – Mondoo TA input management UI
require(['jquery'], function ($) {
    if (!$('#mondoo-app').length) { return; }

    var APP        = 'TA-mondoo';
    var INPUT_TYPE = 'mondoo_input';
    var LOCALE     = window.location.pathname.split('/')[1] || 'en-US';
    var INPUTS_URL = '/' + LOCALE + '/splunkd/__raw/servicesNS/nobody/' + APP + '/data/inputs/' + INPUT_TYPE;

    function getCsrf() {
        var m = document.cookie.match(/splunkweb_csrf_token_\d+=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function apiGet(url) {
        return $.ajax({
            url: url,
            method: 'GET',
            data: { output_mode: 'json', count: 0 },
            headers: { 'X-Splunk-Form-Key': getCsrf(), 'X-Requested-With': 'XMLHttpRequest' }
        });
    }

    function apiPost(url, data) {
        return $.ajax({
            url: url,
            method: 'POST',
            data: $.extend({ output_mode: 'json' }, data),
            headers: { 'X-Splunk-Form-Key': getCsrf(), 'X-Requested-With': 'XMLHttpRequest' }
        });
    }

    function apiDelete(url) {
        return $.ajax({
            url: url,
            method: 'DELETE',
            data: { output_mode: 'json' },
            headers: { 'X-Splunk-Form-Key': getCsrf(), 'X-Requested-With': 'XMLHttpRequest' }
        });
    }

    function showAlert(msg, type) {
        var $a = $('#mondoo-alert');
        $a.removeClass('mondoo-alert-success mondoo-alert-error')
          .addClass(type === 'success' ? 'mondoo-alert-success' : 'mondoo-alert-error')
          .text(msg).show();
        if (type === 'success') { setTimeout(function () { $a.fadeOut(); }, 4000); }
    }

    function clearAlert() { $('#mondoo-alert').hide().text(''); }

    function escHtml(str) {
        return String(str || '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function extractError(xhr) {
        try {
            var b = JSON.parse(xhr.responseText);
            return (b.messages && b.messages[0] && b.messages[0].text) || xhr.responseText.substring(0, 300);
        } catch (e) { return xhr.responseText ? xhr.responseText.substring(0, 300) : ('HTTP ' + xhr.status); }
    }

    function loadInputs() {
        apiGet(INPUTS_URL).done(function (data) {
            renderTable((data && data.entry) ? data.entry : []);
        }).fail(function (xhr) {
            var msg = 'Failed to load inputs (HTTP ' + xhr.status + '): ' + extractError(xhr);
            $('#inputs-tbody').html('<tr><td colspan="6" class="mondoo-error">' + escHtml(msg) + '</td></tr>');
        });
    }

    function renderTable(entries) {
        var $tbody = $('#inputs-tbody');
        if (!entries.length) {
            $tbody.html('<tr><td colspan="6" class="mondoo-empty">No inputs configured. Click <b>+ Add New Input</b> to get started.</td></tr>');
            return;
        }
        var rows = entries.map(function (entry) {
            var c     = entry.content || {};
            var name  = entry.name || '';
            var label = name.replace(/^[^:]+:\/\//, '');
            var dis   = c.disabled === true || c.disabled === '1' || c.disabled === 'true';
            var badge = dis
                ? '<span class="mondoo-badge mondoo-badge-disabled">Disabled</span>'
                : '<span class="mondoo-badge mondoo-badge-enabled">Enabled</span>';
            return '<tr>' +
                '<td>' + escHtml(label) + '</td>' +
                '<td>' + escHtml(c.log_types || 'audit') + '</td>' +
                '<td>' + escHtml(c.index || 'main') + '</td>' +
                '<td>' + escHtml(c.interval || '300') + '</td>' +
                '<td>' + badge + '</td>' +
                '<td class="mondoo-actions">' +
                  '<button class="btn btn-secondary btn-sm mondoo-btn-edit" data-name="' + escHtml(name) + '">Edit</button> ' +
                  '<button class="btn btn-secondary btn-sm mondoo-btn-toggle" data-name="' + escHtml(name) + '" data-disabled="' + dis + '">' + (dis ? 'Enable' : 'Disable') + '</button> ' +
                  '<button class="btn btn-danger btn-sm mondoo-btn-delete" data-name="' + escHtml(name) + '">Delete</button>' +
                '</td>' +
            '</tr>';
        });
        $tbody.html(rows.join(''));
    }

    function showForm(editName, content) {
        clearAlert();
        $('#form-title').text(editName ? 'Edit Input: ' + editName.replace(/^[^:]+:\/\//, '') : 'Add New Input');
        $('#field-edit-name').val(editName || '');
        $('#field-name').val(editName ? editName.replace(/^[^:]+:\/\//, '') : '').prop('disabled', !!editName);
        var allTypes = ['audit','assets','vulnerabilities','advisories','checks'];
        if (content) {
            $('#field-blob').val(content.mondoo_config_blob || '');
            $('#field-index').val(content.index || 'main');
            $('#field-interval').val(content.interval || '300');
            $('#field-lookback').val(content.initial_lookback_days || '7');
            $('#field-mrn').val(content.resource_mrn || '');
            var lts = (content.log_types || 'audit').split(',').map(function (s) { return s.trim(); });
            allTypes.forEach(function (t) { $('#lt-' + t).prop('checked', lts.indexOf(t) >= 0); });
        } else {
            $('#input-form')[0].reset();
            allTypes.forEach(function (t) { $('#lt-' + t).prop('checked', t === 'audit'); });
            $('#field-index').val('main');
            $('#field-interval').val('300');
            $('#field-lookback').val('7');
        }
        $('#input-form-panel').slideDown(200);
        $('html,body').animate({ scrollTop: $('#input-form-panel').offset().top - 80 }, 300);
    }

    function hideForm() { $('#input-form-panel').slideUp(200); $('#input-form')[0].reset(); }

    function collectLogTypes() {
        var lt = [];
        $('.mondoo-checkboxes input[type=checkbox]:checked').each(function () { lt.push($(this).val()); });
        return lt.length ? lt.join(',') : 'audit';
    }

    function saveInput(editName, label, formData) {
        if (!formData.mondoo_config_blob) { showAlert('Mondoo Config Blob is required.', 'error'); return; }
        if (!label) { showAlert('Input Name is required.', 'error'); return; }
        var $btn = $('#btn-save').prop('disabled', true).text('Saving\u2026');
        var done = function () { showAlert('Input "' + label + '" saved.', 'success'); hideForm(); loadInputs(); };
        var fail = function (xhr) { showAlert('Save failed: ' + extractError(xhr), 'error'); };
        var alw  = function () { $btn.prop('disabled', false).text('Save'); };
        if (editName) {
            apiPost(INPUTS_URL + '/' + encodeURIComponent(editName), formData).done(done).fail(fail).always(alw);
        } else {
            apiPost(INPUTS_URL, $.extend({ name: label }, formData)).done(done).fail(fail).always(alw);
        }
    }

    $(document).on('click', '#btn-add-input', function () { showForm(null, null); });
    $(document).on('click', '#btn-cancel', function () { hideForm(); clearAlert(); });
    $(document).on('submit', '#input-form', function (e) {
        e.preventDefault();
        var editName = $('#field-edit-name').val();
        var label    = editName ? editName.replace(/^[^:]+:\/\//, '') : $('#field-name').val().trim();
        saveInput(editName || null, label, {
            mondoo_config_blob:    $('#field-blob').val().trim(),
            log_types:             collectLogTypes(),
            index:                 $('#field-index').val().trim() || 'main',
            interval:              $('#field-interval').val().trim() || '300',
            initial_lookback_days: $('#field-lookback').val().trim() || '7',
            resource_mrn:          $('#field-mrn').val().trim()
        });
    });
    $(document).on('click', '.mondoo-btn-edit', function () {
        var name = $(this).data('name');
        apiGet(INPUTS_URL + '/' + encodeURIComponent(name)).done(function (data) {
            var entry = data && data.entry && data.entry[0];
            showForm(name, entry ? entry.content : {});
        }).fail(function () { showForm(name, {}); });
    });
    $(document).on('click', '.mondoo-btn-delete', function () {
        var name  = $(this).data('name');
        var label = name.replace(/^[^:]+:\/\//, '');
        if (!confirm('Delete input "' + label + '"? This cannot be undone.')) { return; }
        apiDelete(INPUTS_URL + '/' + encodeURIComponent(name))
            .done(function () { showAlert('Input "' + label + '" deleted.', 'success'); loadInputs(); })
            .fail(function (xhr) { showAlert('Delete failed: ' + extractError(xhr), 'error'); });
    });
    $(document).on('click', '.mondoo-btn-toggle', function () {
        var name   = $(this).data('name');
        var dis    = $(this).data('disabled');
        var action = (dis === true || dis === 'true') ? 'enable' : 'disable';
        apiPost(INPUTS_URL + '/' + encodeURIComponent(name) + '/' + action, {})
            .done(loadInputs)
            .fail(function (xhr) { showAlert('Toggle failed: ' + extractError(xhr), 'error'); });
    });

    loadInputs();
});
