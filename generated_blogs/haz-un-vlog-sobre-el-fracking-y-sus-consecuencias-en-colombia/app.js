document.addEventListener('DOMContentLoaded', () => {
  const button = document.querySelector('.contact-form button');
  if (button) {
    button.addEventListener('click', () => {
      alert('Gracias por tu interes en Haz un vlog sobre el fracking y sus consecuencias en colombia.. Puedes conectar este formulario a WhatsApp, correo o CRM.');
    });
  }
});