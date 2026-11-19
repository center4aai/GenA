import { useEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHeader } from '@/shared/ui/PageHeader';
import { usePageToc } from '@/shared/lib/toc';
import { usePageTitle } from '@/shared/i18n/usePageTitle';

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="card scroll-mt-6">
      <h2 className="text-xl font-semibold tracking-tight text-slate-900">{title}</h2>
      <div className="mt-4 space-y-4 text-[15px] leading-relaxed text-slate-600">{children}</div>
    </section>
  );
}

function Code({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[0.85em] text-slate-800">{children}</code>
  );
}

function Callout({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-r-lg border-l-4 border-gena-primary bg-gena-primary/5 px-4 py-3 text-sm text-slate-700">
      {children}
    </div>
  );
}

function Step({ n, title, children, action }: { n: number; title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex gap-4">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gena-primary/10 text-sm font-semibold text-gena-primary">
        {n}
      </div>
      <div className="min-w-0 flex-1">
        <h3 className="font-semibold text-slate-900">{title}</h3>
        <div className="mt-1 space-y-2 text-[15px] leading-relaxed text-slate-600">{children}</div>
        {action && <div className="mt-3">{action}</div>}
      </div>
    </div>
  );
}

export function DocsPage() {
  const { t } = useTranslation(['docs', 'nav', 'common']);
  usePageTitle('userGuide', 'nav');

  const sections = useMemo(
    () => [
      { id: 'about', label: t('docs:tocAbout') },
      { id: 'sensitivity', label: t('docs:tocSensitivity') },
      { id: 'types', label: t('docs:tocTypes') },
      { id: 'howto', label: t('docs:tocHowto') },
      { id: 'gate', label: t('docs:tocGate') },
      { id: 'chunks-storage', label: t('docs:tocChunks') },
      { id: 'validation', label: t('docs:tocValidation') },
    ],
    [t],
  );
  usePageToc(sections);

  const { hash } = useLocation();
  useEffect(() => {
    if (!hash) return;
    const id = hash.replace(/^#/, '');
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [hash]);

  return (
    <div>
      <PageHeader title={t('docs:title')} subtitle={t('docs:subtitle')} />

      <div className="mx-auto max-w-3xl">
        <div className="min-w-0 space-y-6">
          <Section id="about" title={t('docs:tocAbout')}>
            <p>{t('docs:aboutP1')}</p>
            <p className="font-medium text-slate-700">{t('docs:aboutContributions')}</p>
            <ol className="list-decimal space-y-2 pl-5 marker:text-slate-400">
              <li>
                <strong className="text-slate-800">{t('docs:aboutC1Title')}</strong> {t('docs:aboutC1')}
              </li>
              <li>
                <strong className="text-slate-800">{t('docs:aboutC2Title')}</strong> {t('docs:aboutC2')}
              </li>
              <li>
                <strong className="text-slate-800">{t('docs:aboutC3Title')}</strong> {t('docs:aboutC3')}
              </li>
            </ol>
          </Section>

          <Section id="sensitivity" title={t('docs:tocSensitivity')}>
            <p>{t('docs:sensIntro')}</p>
            <div className="overflow-hidden rounded-xl border border-slate-200">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                    <th className="px-4 py-2">{t('docs:sensLevel')}</th>
                    <th className="px-4 py-2">{t('docs:sensDesc')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  <tr>
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-emerald-700">{t('docs:sens1')}</td>
                    <td className="px-4 py-3">{t('docs:sens1Desc')}</td>
                  </tr>
                  <tr>
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-amber-700">{t('docs:sens2')}</td>
                    <td className="px-4 py-3">{t('docs:sens2Desc')}</td>
                  </tr>
                  <tr>
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-red-700">{t('docs:sens3')}</td>
                    <td className="px-4 py-3">{t('docs:sens3Desc')}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p>{t('docs:sensOutro')}</p>
          </Section>

          <Section id="types" title={t('docs:tocTypes')}>
            <p>{t('docs:typesIntro')}</p>
            <ul className="space-y-2">
              <li className="flex gap-2">
                <span className="text-gena-primary">•</span>
                <span>
                  <strong className="text-slate-800">{t('docs:typeOne')}</strong> (<Code>one</Code>) — {t('docs:typeOneDesc')}
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-gena-primary">•</span>
                <span>
                  <strong className="text-slate-800">{t('docs:typeMulti')}</strong> (<Code>multi</Code>) — {t('docs:typeMultiDesc')}
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-gena-primary">•</span>
                <span>
                  <strong className="text-slate-800">{t('docs:typeOpen')}</strong> (<Code>open</Code>) — {t('docs:typeOpenDesc')}
                </span>
              </li>
            </ul>
          </Section>

          <Section id="howto" title={t('docs:tocHowto')}>
            <div className="space-y-6">
              <Step
                n={1}
                title={t('docs:howto1Title')}
                action={<Link to="/data_preprocessing" className="btn-primary inline-block">{t('docs:howto1Action')}</Link>}
              >
                <p>{t('docs:howto1Body')}</p>
              </Step>

              <Step n={2} title={t('docs:howto2Title')}>
                <p>{t('docs:howto2Body')}</p>
                <Callout>{t('docs:howto2Note')}</Callout>
                <ul className="list-disc space-y-1 pl-5 marker:text-slate-400">
                  <li>{t('docs:howto2Models')}</li>
                  <li>{t('docs:howto2Queue')}</li>
                </ul>
              </Step>

              <Step n={3} title={t('docs:howto3Title')}>
                <p>{t('docs:howto3Body')}</p>
                <ol className="list-decimal space-y-1 pl-5 marker:text-slate-400">
                  <li>{t('docs:howto3Chunk')}</li>
                  <li>{t('docs:howto3Gate')}</li>
                  <li>{t('docs:howto3Gen')}</li>
                </ol>
                <Callout>{t('docs:howto3Note')}</Callout>
              </Step>

              <Step
                n={4}
                title={t('docs:howto4Title')}
                action={<Link to="/queue_manager" className="btn-secondary inline-block">{t('docs:howto4Action')}</Link>}
              >
                <p>{t('docs:howto4Body')}</p>
              </Step>

              <Step
                n={5}
                title={t('docs:howto5Title')}
                action={<Link to="/dataset_editor" className="btn-secondary inline-block">{t('docs:howto5Action')}</Link>}
              >
                <p>{t('docs:howto5Body')}</p>
              </Step>

              <Step n={6} title={t('docs:howto6Title')}>
                <p>{t('docs:howto6Body')}</p>
              </Step>

              <Step
                n={7}
                title={t('docs:howto7Title')}
                action={<Link to="/statistics" className="btn-secondary inline-block">{t('docs:howto7Action')}</Link>}
              >
                <p>{t('docs:howto7Body')}</p>
              </Step>
            </div>
          </Section>

          <Section id="gate" title={t('docs:tocGate')}>
            <p>{t('docs:gateIntro')}</p>
            <ul className="list-disc space-y-1 pl-5 marker:text-slate-400">
              <li>{t('docs:gateC1')}</li>
              <li>{t('docs:gateC2')}</li>
              <li>{t('docs:gateC3')}</li>
            </ul>
            <p>{t('docs:gateOutro')}</p>
            <Callout>{t('docs:gateNote')}</Callout>
          </Section>

          <Section id="chunks-storage" title={t('docs:tocChunks')}>
            <p>{t('docs:chunksIntro')}</p>
            <p>{t('docs:chunksTable')}</p>
          </Section>

          <Section id="validation" title={t('docs:tocValidation')}>
            <p>{t('docs:validationIntro')}</p>
            <ul className="list-disc space-y-1 pl-5 marker:text-slate-400">
              <li>{t('docs:validation1')}</li>
              <li>{t('docs:validation2')}</li>
              <li>{t('docs:validation3')}</li>
              <li>{t('docs:validation4')}</li>
            </ul>
            <p>{t('docs:validationScoring')}</p>
            <p>{t('docs:validationOutro')}</p>
          </Section>

          <div className="pt-2">
            <Link to="/" className="text-sm font-medium text-gena-primary hover:underline">
              {t('docs:backHome')}
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
