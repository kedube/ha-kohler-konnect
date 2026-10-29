import { css } from "lit";

// How product and fixture drawings look and move. A parent marks a running device with the
// `running` class: the spray falls, the faucet's stream appears, the fan draws air.
export const artStyles = css`
  .art {
    display: block;
    width: 100%;
    height: 100%;
    overflow: visible;
    color: var(--kk-art);
  }

  .art .accent {
    stroke: var(--kk-accent);
  }

  .art .spray line {
    stroke-dasharray: 3 8;
    opacity: 0.85;
  }

  .art .stream {
    opacity: 0;
    stroke: var(--kk-accent);
    stroke-dasharray: 5 6;
    transition: opacity 400ms var(--kk-ease);
  }

  .running .art .spray line,
  .running .fixture .spray line {
    stroke: var(--kk-accent);
    animation: kk-fall 0.9s linear infinite;
  }

  .running .art .stream {
    opacity: 1;
    animation: kk-fall 0.6s linear infinite;
  }

  @keyframes kk-fall {
    to {
      stroke-dashoffset: -22;
    }
  }
`;
