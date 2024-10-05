document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('uploadForm');
    const resultDiv = document.getElementById('result');
    const emailBtn = document.getElementById('emailBtn');
    const emailModal = document.getElementById('emailModal');
    const emailInput = document.getElementById('emailInput');
    const sendEmailBtn = document.getElementById('sendEmailBtn');
    const closeModalBtn = document.getElementById('closeModalBtn');
    let ocr_result, diagnosis;

    form.addEventListener('submit', function(e) {
        e.preventDefault();
        const formData = new FormData(form);
        
        // Show loading indicator
        resultDiv.innerHTML = '<p>Loading...</p>';

        fetch('/chat', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                resultDiv.textContent = 'Error: ' + data.error;
                return;
            }
            ocr_result = data.response;
            diagnosis = data.diagnosis;
            resultDiv.innerHTML = `
                <h2 class="text-xl font-bold mb-2">Results:</h2>
                <p><strong>OCR Result:</strong> ${ocr_result}</p>
                <p><strong>Diagnosis:</strong> ${diagnosis}</p>
            `;
            emailBtn.style.display = 'block';

            // Add JSON display and copy functionality
            const jsonString = JSON.stringify(data, null, 2);
            resultDiv.innerHTML += `
                <h3>JSON Result:</h3>
                <div class="json-display">${formatJSON(jsonString)}</div>
                <button class="copy-btn" onclick="copyToClipboard()">Copy JSON</button>
            `;
        })
        .catch(error => {
            console.error('Error:', error);
            resultDiv.textContent = 'An error occurred. Please try again.';
        });
    });

    emailBtn.addEventListener('click', function() {
        emailModal.style.display = 'flex';
    });

    closeModalBtn.addEventListener('click', function() {
        emailModal.style.display = 'none';
    });

    sendEmailBtn.addEventListener('click', function() {
        const email = emailInput.value;
        fetch('/send_email', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                email: email,
                ocr_result: ocr_result,
                diagnosis: diagnosis
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
                return;
            }
            alert(data.message);
            emailModal.style.display = 'none';
            emailInput.value = '';  // Clear email input field
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Failed to send email. Please try again.');
        });
    });

    // Close modal when clicking outside of it
    window.onclick = function(event) {
        if (event.target == emailModal) {
            emailModal.style.display = 'none';
        }
    }

    // JSON formatting function
    function formatJSON(json) {
        return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
            var cls = 'json-number';
            if (/^"/.test(match)) {
                if (/:$/.test(match)) {
                    cls = 'json-key';
                } else {
                    cls = 'json-string';
                }
            } else if (/true|false/.test(match)) {
                cls = 'json-boolean';
            } else if (/null/.test(match)) {
                cls = 'json-null';
            }
            return '<span class="' + cls + '">' + match + '</span>';
        });
    }

    // Copy JSON to clipboard function
    window.copyToClipboard = function() {
        var jsonDisplay = document.querySelector('.json-display');
        var range = document.createRange();
        range.selectNode(jsonDisplay);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
        document.execCommand('copy');
        window.getSelection().removeAllRanges();
        alert('JSON copied to clipboard!');
    }
});
