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
        
        fetch('/chat', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            ocr_result = data.response;
            diagnosis = data.diagnosis;
            resultDiv.innerHTML = `
                <h2 class="text-xl font-bold mb-2">Results:</h2>
                <p><strong>OCR Result:</strong> ${ocr_result}</p>
                <p><strong>Diagnosis:</strong> ${diagnosis}</p>
            `;
            emailBtn.style.display = 'block';
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
            alert(data.message);
            emailModal.style.display = 'none';
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Failed to send email. Please try again.');
        });
    });
});
