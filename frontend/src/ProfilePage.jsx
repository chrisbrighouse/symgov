import { useEffect, useMemo, useRef, useState } from 'react';
import { downgradeCurrentSubscription, fetchProfile, upgradeCurrentSubscription } from './api.js';

function formatDate(value) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('en-GB', { dateStyle: 'long', timeZone: 'UTC' }).format(new Date(`${value}T00:00:00Z`));
}

function formatMoney(pence, currency = 'GBP') {
  return new Intl.NumberFormat('en-GB', { style: 'currency', currency }).format((pence || 0) / 100);
}

export default function ProfilePage({ auth }) {
  const [profile, setProfile] = useState(null);
  const [years, setYears] = useState(1);
  const [reviewing, setReviewing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const confirmationRef = useRef(null);

  useEffect(() => {
    let active = true;
    fetchProfile().then((result) => {
      if (!active) return;
      if (result.ok) {
        setProfile(result.payload);
        setYears(result.payload?.plan?.minimumYears || 1);
      } else {
        setError(result.message || 'Your profile could not be loaded.');
      }
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (reviewing) confirmationRef.current?.focus();
  }, [reviewing]);

  const selectedOption = useMemo(
    () => profile?.plan?.upgradeOptions?.find((option) => option.years === Number(years)),
    [profile, years]
  );

  if (!profile && !error) {
    return <section className="profile-page"><p>Loading your profile…</p></section>;
  }

  if (!profile) {
    return <section className="profile-page"><p className="form-message error" role="alert">{error}</p></section>;
  }

  const { user, plan } = profile;
  const subscription = user.subscription;
  const isPlus = subscription.tier === 'plus';
  const allowedRange = `${plan.minimumYears}–${plan.maximumYears} years`;

  const applyResult = async (result, successMessage) => {
    if (!result.ok) {
      setError(result.message || 'The subscription could not be changed.');
      return;
    }
    setProfile(result.payload);
    await auth.refresh();
    setReviewing(null);
    setMessage(successMessage);
  };

  const confirmUpgrade = async () => {
    setBusy(true);
    setError('');
    const result = await upgradeCurrentSubscription({ years: Number(years), confirmed: true });
    await applyResult(result, 'Plus is active. Confirmation emails have been queued.');
    setBusy(false);
  };

  const confirmDowngrade = async () => {
    setBusy(true);
    setError('');
    const result = await downgradeCurrentSubscription({ confirmed: true });
    await applyResult(result, 'Your subscription is now Free. Confirmation emails have been queued.');
    setBusy(false);
  };

  return (
    <section className="profile-page" aria-labelledby="profile-title">
      <div className="profile-heading">
        <p className="eyebrow">Account</p>
        <h2 id="profile-title">Your profile</h2>
        <p>View your identity and manage your Symgov subscription.</p>
      </div>

      <div className="profile-grid">
        <article className="profile-card">
          <p className="eyebrow">Identity</p>
          <dl className="profile-details">
            <div><dt>Name</dt><dd>{user.displayName}</dd></div>
            <div><dt>Email</dt><dd>{user.email}</dd></div>
          </dl>
        </article>

        <article className="profile-card profile-subscription-card">
          <div className="profile-card-title">
            <div>
              <p className="eyebrow">Subscription</p>
              <h3>Current tier: {isPlus ? 'Plus' : 'Free'}</h3>
            </div>
            <span className={`subscription-tier-pill ${subscription.tier}`}>{isPlus ? 'Plus' : 'Free'}</span>
          </div>

          {isPlus ? (
            <dl className="profile-details">
              <div><dt>Start date</dt><dd>{formatDate(subscription.startedOn)}</dd></div>
              <div><dt>Expiry date</dt><dd>{subscription.isProtected ? 'Perpetual' : formatDate(subscription.expiresOn)}</dd></div>
            </dl>
          ) : <p>Free has no subscription expiry date.</p>}

          {!isPlus ? (
            <div className="profile-subscription-action">
              <label>
                Years of Plus <span className="catalog-optional-label">Available: {allowedRange}</span>
                <select value={years} onChange={(event) => { setYears(Number(event.target.value)); setReviewing(null); }} disabled={busy}>
                  {plan.upgradeOptions.map((option) => (
                    <option key={option.years} value={option.years}>{option.years} {option.years === 1 ? 'year' : 'years'}</option>
                  ))}
                </select>
              </label>
              <div className="profile-price-summary">
                <span>{formatMoney(plan.annualPricePence, plan.currency)} per year</span>
                <strong>{formatMoney(selectedOption?.totalPricePence, plan.currency)} total</strong>
                <span>Plus until {formatDate(selectedOption?.expiresOn)}</span>
              </div>
              <p className="profile-payment-notice">No payment will be taken for this initial release.</p>
              {reviewing === 'upgrade' ? (
                <div ref={confirmationRef} tabIndex={-1} className="profile-confirmation" role="group" aria-live="polite" aria-label="Confirm upgrade">
                  <p>Confirm {years} {years === 1 ? 'year' : 'years'} of Plus for {formatMoney(selectedOption?.totalPricePence, plan.currency)}.</p>
                  <div className="action-stack horizontal">
                    <button type="button" className="primary-button" onClick={confirmUpgrade} disabled={busy}>{busy ? 'Activating…' : 'Confirm upgrade'}</button>
                    <button type="button" className="ghost-button" onClick={() => setReviewing(null)} disabled={busy}>Back</button>
                  </div>
                </div>
              ) : <button type="button" className="primary-button" onClick={() => setReviewing('upgrade')} disabled={busy}>Review upgrade</button>}
            </div>
          ) : subscription.isProtected ? (
            <p className="profile-protected-note">This protected owner subscription is perpetual and cannot be downgraded.</p>
          ) : (
            <div className="profile-subscription-action danger-zone">
              <h4>Downgrade to Free</h4>
              <p>This takes effect immediately. You will surrender any remaining subscription time and Plus-dependent roles will be removed.</p>
              {reviewing === 'downgrade' ? (
                <div ref={confirmationRef} tabIndex={-1} className="profile-confirmation" role="group" aria-live="polite" aria-label="Confirm immediate downgrade">
                  <p>This cannot be scheduled or withdrawn. Your account will become Free immediately.</p>
                  <div className="action-stack horizontal">
                    <button type="button" className="action-button danger" onClick={confirmDowngrade} disabled={busy}>{busy ? 'Downgrading…' : 'Confirm immediate downgrade'}</button>
                    <button type="button" className="ghost-button" onClick={() => setReviewing(null)} disabled={busy}>Keep Plus</button>
                  </div>
                </div>
              ) : <button type="button" className="action-button danger" onClick={() => setReviewing('downgrade')} disabled={busy}>Review immediate downgrade</button>}
            </div>
          )}

          {message ? <p className="form-message success" role="status">{message}</p> : null}
          {error ? <p className="form-message error" role="alert">{error}</p> : null}
        </article>
      </div>
    </section>
  );
}
