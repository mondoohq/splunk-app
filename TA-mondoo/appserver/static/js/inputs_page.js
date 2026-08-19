/**
 * inputs_page.js – Mondoo TA input management UI.
 *
 * Handles listing, creating, editing, and deleting mondoo_input instances
 * via Splunk's REST API proxy (/en-US/splunkd/__raw/...).
 *
 * Uses jQuery only (no Splunk JS module dependency) — this page is a plain
 * HTML view, not a SimpleXML dashboard, so the heavier framework isn't
 * needed for DOM and AJAX work.
 */
require(['jquery'], function ($) {
    'use strict';

    // Defer until the DOM is ready.
    $(function () {

    var APP        = 'TA-mondoo';
    var INPUT_TYPE = 'mondoo_input';
    var API_BASE   = '/en-US/splunkd/__raw/servicesNS/nobody/' + APP;
    var INPUTS_URL = API_BASE + '/data/inputs/' + INPUT_TYPE;

    // -----------------------------------------------------------------------
    // CSRF token (Splunk stores it in a cookie)
    // -----------------------------------------------------------------------
    function getCsrf() {
        var m = document.cookie.match(/splunkweb_csrf_token_\d+=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    // -----------------------------------------------------------------------
    // Low-level REST helpers
    // -----------------------------------------------------------------------
    function apiGet(url) {
        return $.ajax({
            url: url + '?output_mode=json&count=0',
            method: 'GET'
        });
    }

    function apiPost(url, data) {
        data['splunk_form_key'] = getCsrf();
        return $.ajax({
            url: url + '?output_mode=json',
            method: 'POST',
            data: data,
            contentType: 'application/x-www-form-urlencoded'
        });
    }

    function apiDelete(url) {
        return $.ajax({
            url: url + '?output_mode=json',
            method: 'DELETE',
            headers: { 'X-Splunk-Form-Key': getCsrf() }
        });
    }

    // -----------------------------------------------------------------------
    // Alert helpers
    // -----------------------------------------------------------------------
    function showAlert(msg, type) {
        // type: 'success' | 'error'
        var $a = $('#mondoo-alert');
        $a.removeClass('mondoo-alert-success mondoo-alert-error')
          .addClass(type === 'success' ? 'mondoo-alert-success' : 'mondoo-alert-error')
          .text(msg)
          .show();
        if (type === 'success') {
            setTimeout(function () { $a.fadeOut(); }, 4000);
        }
    }

    function clearAlert() {
        $('#mondoo-alert').hide().text('');
    }

    // -----------------------------------------------------------------------
    // Render the inputs table
    // -----------------------------------------------------------------------
    function loadInputs() {
        apiGet(INPUTS_URL).done(function (data) {
            var entries = (data && data.entry) ? data.entry : [];
            renderTable(entries);
        }).fail(function (xhr) {
            var msg = xhr.status === 404
                ? 'Modular input not found. Ensure mondoo_input.py is executable on the Splunk server (chmod +x).'
                : 'Failed to load inputs (HTTP ' + xhr.status + '): ' + (xhr.responseText || '');
            $('#inputs-tbody').html(
                '<tr><td colspan="6" class="mondoo-error">' + escHtml(msg) + '</td></tr>'
            );
        });
    }

    function renderTable(entries) {
        var $tbody = $('#inputs-tbody');
        if (!entries.length) {
            $tbody.html('<tr><td colspan="6" class="mondoo-empty">No inputs configured. Click <b>+ Add New Input</b> to get started.</td></tr>');
            return;
        }

        var rows = entries.map(function (entry) {
            var c      = entry.content || {};
            var name   = entry.name || '';
            // Strip stanza prefix ("mondoo_input://name" → "name")
            var label  = name.replace(/^[^:]+:\/\//, '');
            var status = c.disabled === true || c.disabled === '1' || c.disabled === 'true'
                       ? '<span class="mondoo-badge mondoo-badge-disabled">Disabled</span>'
                       : '<span class="mondoo-badge mondoo-badge-enabled">Enabled</span>';

            return '<tr data-name="' + escAttr(name) + '" data-label="' + escAttr(label) + '">' +
                '<td>' + escHtml(label) + '</td>' +
                '<td>' + escHtml(c.log_types || 'audit') + '</td>' +
                '<td>' + escHtml(c.index || 'main') + '</td>' +
                '<td>' + escHtml(c.interval || '300') + '</td>' +
                '<td>' + status + '</td>' +
                '<td class="mondoo-actions">' +
                  '<button class="btn btn-secondary btn-sm mondoo-btn-edit" data-name="' + escAttr(name) + '">Edit</button> ' +
                  '<button class="btn btn-secondary btn-sm mondoo-btn-toggle" data-name="' + escAttr(name) + '" data-disabled="' + escAttr(String(c.disabled || 'false')) + '">' +
                    (c.disabled === true || c.disabled === '1' || c.disabled === 'true' ? 'Enable' : 'Disable') +
                  '</button> ' +
                  '<button class="btn btn-danger btn-sm mondoo-btn-delete" data-name="' + escAttr(name) + '">Delete</button>' +
                '</td>' +
            '</tr>';
        });

        $tbody.html(rows.join(''));
    }

    // -----------------------------------------------------------------------
    // Form helpers
    // -----------------------------------------------------------------------
    function showForm(editName, content) {
        clearAlert();
        var isEdit = !!editName;
        $('#form-title').text(isEdit ? 'Edit Input: ' + editName : 'Add New Input');
        $('#field-edit-name').val(editName || '');
        $('#field-name').val(editName || '').prop('disabled', isEdit);

        if (content) {
            $('#field-blob').val(content.mondoo_config_blob || '');
            $('#field-index').val(content.index || 'main');
            $('#field-interval').val(content.interval || '300');
            $('#field-lookback').val(content.initial_lookback_days || '7');
            $('#field-mrn').val(content.resource_mrn || '');

            // Log types checkboxes
            var logTypes = (content.log_types || 'audit').split(',').map(function (s) { return s.trim(); });
            $('#lt-audit').prop('checked', logTypes.indexOf('audit') >= 0);
        } else {
            $('#input-form')[0].reset();
            $('#lt-audit').prop('checked', true);
            $('#field-index').val('main');
            $('#field-interval').val('300');
            $('#field-lookback').val('7');
        }

        $('#input-form-panel').slideDown(200);
        $('html, body').animate({ scrollTop: $('#input-form-panel').offset().top - 80 }, 300);
    }

    function hideForm() {
        $('#input-form-panel').slideUp(200);
        $('#input-form')[0].reset();
    }

    function collectFormData() {
        var logTypes = [];
        $('#mondoo-checkboxes input[type=checkbox]:checked, .mondoo-checkboxes input[type=checkbox]:checked').each(function () {
            logTypes.push($(this).val());
        });
        if (!logTypes.length) { logTypes = ['audit']; }

        return {
            mondoo_config_blob:      $('#field-blob').val().trim(),
            log_types:               logTypes.join(','),
            index:                   $('#field-index').val().trim() || 'main',
            interval:                $('#field-interval').val().trim() || '300',
            initial_lookback_days:   $('#field-lookback').val().trim() || '7',
            resource_mrn:            $('#field-mrn').val().trim()
        };
    }

    // -----------------------------------------------------------------------
    // Save (create or update)
    // -----------------------------------------------------------------------
    function saveInput(editName, label, formData) {
        if (!formData.mondoo_config_blob) {
            showAlert('Mondoo Config Blob is required.', 'error');
            return;
        }
        if (!label) {
            showAlert('Input Name is required.', 'error');
            return;
        }

        var $btn = $('#btn-save').prop('disabled', true).text('Saving…');

        if (editName) {
            // Update existing
            apiPost(INPUTS_URL + '/' + encodeURIComponent(editName), formData)
                .done(function () {
                    showAlert('Input "' + label + '" updated successfully.', 'success');
                    hideForm();
                    loadInputs();
                })
                .fail(function (xhr) {
                    showAlert('Save failed: ' + extractError(xhr), 'error');
                })
                .always(function () { $btn.prop('disabled', false).text('Save'); });
        } else {
            // Create new
            var createData = $.extend({ name: label }, formData);
            apiPost(INPUTS_URL, createData)
                .done(function () {
                    showAlert('Input "' + label + '" created successfully.', 'success');
                    hideForm();
                    loadInputs();
                })
                .fail(function (xhr) {
                    showAlert('Create failed: ' + extractError(xhr), 'error');
                })
                .always(function () { $btn.prop('disabled', false).text('Save'); });
        }
    }

    // -----------------------------------------------------------------------
    // Delete
    // -----------------------------------------------------------------------
    function deleteInput(name) {
        var label = name.replace(/^[^:]+:\/\//, '');
        if (!confirm('Delete input "' + label + '"? This cannot be undone.')) { return; }

        apiDelete(INPUTS_URL + '/' + encodeURIComponent(name))
            .done(function () {
                showAlert('Input "' + label + '" deleted.', 'success');
                loadInputs();
            })
            .fail(function (xhr) {
                showAlert('Delete failed: ' + extractError(xhr), 'error');
            });
    }

    // -----------------------------------------------------------------------
    // Enable / Disable
    // -----------------------------------------------------------------------
    function toggleInput(name, currentlyDisabled) {
        var action = currentlyDisabled ? 'enable' : 'disable';
        apiPost(INPUTS_URL + '/' + encodeURIComponent(name) + '/' + action, {})
            .done(function () {
                loadInputs();
            })
            .fail(function (xhr) {
                showAlert('Toggle failed: ' + extractError(xhr), 'error');
            });
    }

    // -----------------------------------------------------------------------
    // Load entry for editing (fetch full content)
    // -----------------------------------------------------------------------
    function loadEntry(name, callback) {
        apiGet(INPUTS_URL + '/' + encodeURIComponent(name))
            .done(function (data) {
                var entry = data && data.entry && data.entry[0];
                callback(entry ? entry.content : {});
            })
            .fail(function () { callback({}); });
    }

    // -----------------------------------------------------------------------
    // Utility
    // -----------------------------------------------------------------------
    function escHtml(str) {
        return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function escAttr(str) { return escHtml(str); }

    function extractError(xhr) {
        try {
            var body = JSON.parse(xhr.responseText);
            return body.messages && body.messages[0] && body.messages[0].text
                 ? body.messages[0].text
                 : xhr.responseText.substring(0, 300);
        } catch (e) {
            return xhr.responseText ? xhr.responseText.substring(0, 300) : ('HTTP ' + xhr.status);
        }
    }

    // -----------------------------------------------------------------------
    // Event bindings
    // -----------------------------------------------------------------------
    $(document).on('click', '#btn-add-input', function () {
        showForm(null, null);
    });

    $(document).on('click', '#btn-cancel', function () {
        hideForm();
        clearAlert();
    });

    $(document).on('submit', '#input-form', function (e) {
        e.preventDefault();
        var editName = $('#field-edit-name').val();
        var label    = editName
            ? editName.replace(/^[^:]+:\/\//, '')
            : $('#field-name').val().trim();
        saveInput(editName || null, label, collectFormData());
    });

    $(document).on('click', '.mondoo-btn-edit', function () {
        var name = $(this).data('name');
        loadEntry(name, function (content) {
            showForm(name, content);
        });
    });

    $(document).on('click', '.mondoo-btn-delete', function () {
        deleteInput($(this).data('name'));
    });

    $(document).on('click', '.mondoo-btn-toggle', function () {
        var name      = $(this).data('name');
        var disabled  = $(this).data('disabled');
        var isDisabled = disabled === true || disabled === '1' || disabled === 'true';
        toggleInput(name, isDisabled);
    });

    // -----------------------------------------------------------------------
    // Init
    // -----------------------------------------------------------------------
    loadInputs();

    }); // end $(function () { ... }) — DOM-ready handler
});
