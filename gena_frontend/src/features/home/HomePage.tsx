import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { usePageTitle } from '@/shared/i18n/usePageTitle';

export function HomePage() {
  const { t } = useTranslation(['home', 'nav', 'common']);
  usePageTitle('home', 'nav');

  const steps = [
    { title: t('home:step1Title'), desc: t('home:step1Desc'), link: t('home:step1Link'), to: '/data_preprocessing' },
    { title: t('home:step2Title'), desc: t('home:step2Desc'), link: t('home:step2Link'), to: '/queue_manager' },
    { title: t('home:step3Title'), desc: t('home:step3Desc'), link: t('home:step3Link'), to: '/dataset_editor' },
  ];

  return (
    <div>
      <div className="flex flex-col items-center text-center">
        <img
          src="/logo.png"
          alt={t('common:logoAlt')}
          className="mb-6 h-40 w-auto sm:h-48 lg:h-56"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = 'none';
          }}
        />
        <h1 className="text-4xl font-bold text-slate-900">{t('home:title')}</h1>
        <p className="page-subtitle mx-auto mt-2 max-w-3xl">{t('home:subtitle')}</p>
        <Link to="/data_preprocessing" className="btn-primary mt-6">
          {t('home:cta')}
        </Link>
      </div>

      <hr className="my-8 border-slate-200" />

      <h2 className="mb-2 text-xl font-semibold">{t('home:quickStart')}</h2>
      <p className="mb-4 text-slate-600">{t('home:quickStartHint')}</p>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {steps.map((step) => (
          <div key={step.title} className="card flex min-h-[180px] flex-col bg-stone-50">
            <h4 className="font-semibold">{step.title}</h4>
            <p className="mt-2 flex-1 text-sm text-slate-600">{step.desc}</p>
            <Link to={step.to} className="mt-3 text-sm font-medium text-gena-primary hover:underline">
              {step.link}
            </Link>
          </div>
        ))}
      </div>

      <hr className="my-8 border-slate-200" />
      <Link to="/docs#howto" className="text-gena-primary hover:underline">
        {t('home:fullDocs')}
      </Link>

      <details className="mt-8 card">
        <summary className="cursor-pointer font-semibold">{t('home:aboutTitle')}</summary>
        <div className="prose prose-sm mt-4 max-w-none text-slate-600">
          <p>{t('home:aboutP1')}</p>
          <p>
            <strong>{t('home:aboutP2')}</strong>
          </p>
          <p>{t('home:aboutP3')}</p>
          <ul className="list-disc pl-5">
            <li>{t('home:aboutL1')}</li>
            <li>{t('home:aboutL2')}</li>
            <li>{t('home:aboutL3')}</li>
          </ul>
          <p>{t('home:aboutP4')}</p>
          <p>
            <Link to="/docs" className="text-gena-primary hover:underline">
              {t('home:aboutMore')}
            </Link>
          </p>
        </div>
      </details>
    </div>
  );
}
