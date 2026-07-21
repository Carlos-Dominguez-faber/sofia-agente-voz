/**
 * Branding — the one file to edit when this panel is handed to another clinic.
 *
 * Everything the client sees as "theirs" lives here: the name, the colours, the
 * logo, the contact line in the footer. Nothing else in the codebase should
 * hardcode a clinic name or a brand colour, so installing this for the next
 * business is one file, not a search-and-replace.
 *
 * Deliberately NOT here: anything that identifies a system or a credential.
 * Those live in the backend config and in the environment.
 */

export type Branding = {
  /** Business name, shown in the header and the browser tab. */
  clinicName: string;
  /** Short line under the name. Keep it to a few words. */
  tagline: string;
  /** Name of the voice agent, used in copy throughout the panel. */
  agentName: string;
  /**
   * Emoji or short text used as the logo mark. Swap for an <img> in Header.tsx
   * when a real logo file exists — keeping it text means no asset pipeline is
   * needed to install this for a new clinic.
   */
  logoMark: string;
  /** Brand colours as CSS colour values. Applied as CSS variables at the root. */
  colors: {
    accent: string;
    accentSoft: string;
    hot: string;
    warm: string;
    cold: string;
  };
  /** Locale used to format dates, numbers and durations. */
  locale: string;
  /** Shown in the footer so the clinic knows who to call when something breaks. */
  supportLine: string;
};

export const branding: Branding = {
  clinicName: "Clínica Dental Sonrisa Perfecta",
  tagline: "Panel de recepción",
  agentName: "Sofía",
  logoMark: "🦷",
  colors: {
    accent: "#0d9488",
    accentSoft: "#ccfbf1",
    hot: "#dc2626",
    warm: "#d97706",
    cold: "#0284c7",
  },
  locale: "es-MX",
  supportLine: "¿Algo no cuadra? Escríbenos y lo revisamos.",
};
