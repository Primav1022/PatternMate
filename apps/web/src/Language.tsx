import React, { createContext, useContext, useEffect, useState } from 'react';

export type Language = 'zh' | 'en';
type LanguageValue = { language: Language; setLanguage: (language: Language) => void; t: (zh: string, en: string) => string };

const LanguageContext = createContext<LanguageValue>({ language: 'zh', setLanguage: () => undefined, t: (zh) => zh });

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem('pattern-workbench-language') === 'en' ? 'en' : 'zh');
  useEffect(() => { localStorage.setItem('pattern-workbench-language', language); document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en'; }, [language]);
  return <LanguageContext.Provider value={{ language, setLanguage, t: (zh, en) => language === 'zh' ? zh : en }}>{children}</LanguageContext.Provider>;
}

export const useLanguage = () => useContext(LanguageContext);
