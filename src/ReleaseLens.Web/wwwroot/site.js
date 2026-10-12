document.getElementById('question-form')?.addEventListener('submit', event => {
  const button = event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = 'Asking...';
});
