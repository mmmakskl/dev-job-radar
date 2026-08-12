'use client';

import React from 'react';

export function ConfirmAction({ title, description, onConfirm, onCancel }: { title:string; description:string; onConfirm:()=>void; onCancel:()=>void }) {
  return <div className="modal" role="dialog" aria-modal="true" aria-labelledby="action-title"><div><h2 id="action-title">{title}</h2><p className="muted">{description}</p><div className="actions"><button className="button danger" onClick={onConfirm}>Подтвердить</button><button className="button secondary" onClick={onCancel} autoFocus>Отмена</button></div></div></div>;
}
