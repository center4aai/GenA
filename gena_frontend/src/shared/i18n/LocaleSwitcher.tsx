import { useTranslation } from 'react-i18next';
import { isAppLocale, SUPPORTED_LOCALES, type AppLocale } from './config';

export function LocaleSwitcher({ className = '' }: { className?: string }) {
  const { t, i18n } = useTranslation();
  const current: AppLocale = isAppLocale(i18n.language) ? i18n.language : 'en';

  const setLocale = (locale: AppLocale) => {
    void i18n.changeLanguage(locale);
  };

  return (
    <div
      className={`inline-flex overflow-hidden rounded-md border border-stone-600 text-xs ${className}`}
      role="group"
      aria-label={t('common:language')}
    >
      {SUPPORTED_LOCALES.map((locale) => (
        <button
          key={locale}
          type="button"
          className={`px-2.5 py-1 font-medium transition-colors ${
            current === locale
              ? 'bg-gena-accent text-gena-sidebar'
              : 'text-stone-300 hover:bg-stone-700'
          }`}
          aria-pressed={current === locale}
          onClick={() => setLocale(locale)}
        >
          {t(`common:localeName.${locale}`)}
        </button>
      ))}
    </div>
  );
}
