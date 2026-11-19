import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import { isAppLocale, persistLocale, resolveDefaultLocale } from './config';

type LocaleModule = { default: Record<string, unknown> } | Record<string, unknown>;

const localeModules = import.meta.glob('./locales/*/*.json', { eager: true }) as Record<
  string,
  LocaleModule
>;

function unwrap(mod: LocaleModule): Record<string, unknown> {
  if (mod && typeof mod === 'object' && 'default' in mod) {
    return (mod.default ?? {}) as Record<string, unknown>;
  }
  return (mod ?? {}) as Record<string, unknown>;
}

const resources: Record<string, Record<string, Record<string, unknown>>> = {};
const namespaceSet = new Set<string>();

for (const [path, mod] of Object.entries(localeModules)) {
  // ./locales/en/common.json → locale=en, ns=common
  const match = path.match(/\.\/locales\/([^/]+)\/([^/]+)\.json$/);
  if (!match) continue;
  const [, locale, ns] = match;
  if (!resources[locale]) resources[locale] = {};
  resources[locale][ns] = unwrap(mod);
  namespaceSet.add(ns);
}

const namespaces = Array.from(namespaceSet).sort();
const defaultLocale = resolveDefaultLocale();

void i18n.use(initReactI18next).init({
  resources,
  lng: defaultLocale,
  fallbackLng: 'en',
  defaultNS: 'common',
  ns: namespaces,
  interpolation: { escapeValue: false },
});

persistLocale(defaultLocale);

i18n.on('languageChanged', (lng) => {
  if (isAppLocale(lng)) persistLocale(lng);
});

export default i18n;
export {
  resolveDefaultLocale,
  persistLocale,
  LOCALE_STORAGE_KEY,
  SUPPORTED_LOCALES,
  isAppLocale,
  type AppLocale,
} from './config';
export { formatDisplayDate, formatDisplayDateTime, formatPercent, suggestDatasetName, LOCALE_TO_INTL } from './format';
