class Probe extends Info;
replication
{
  reliable if (Role == ROLE_Authority ? true : false)
    Marker;
}
var int Marker;
defaultproperties
{
}
