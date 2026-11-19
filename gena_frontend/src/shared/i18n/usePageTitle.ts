import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';

/** Sets document.title to "{{page}} — GenA" for the current locale. */
export function usePageTitle(pageKey: string, ns = 'common') {
  const { t, i18n } = useTranslation(ns);
  const { t: tc } = useTranslation('common');

  useEffect(() => {
    const page = t(pageKey);
    document.title = tc('pageTitle', { page });
  }, [t, tc, pageKey, i18n.language]);
}
