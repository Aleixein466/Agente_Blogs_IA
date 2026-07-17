document.addEventListener('DOMContentLoaded', () => {
  const button = document.querySelector('.contact-form button');
  if (button) {
    button.addEventListener('click', () => {
      alert('Gracias por tu interes en El Cambio Climático En El Mundo: guia editorial completa. Puedes conectar este formulario a WhatsApp, correo o CRM.');
    });
  }
});